from unittest import mock

from django.core.exceptions import ValidationError

from ... import settings as app_settings
from ...hardware import FIRMWARE_IMAGE_MAP


class BaseTestModels(object):

    def test_category_str(self):
        c = self.category_model(name='WiFi Hotspot')
        self.assertEqual(str(c), c.name)

    def test_build_str(self):
        c = self._create_category()
        b = self.build_model(category=c, version='0.1')
        self.assertIn(c.name, str(b))
        self.assertIn(b.version, str(b))

    def test_build_str_no_category(self):
        b = self.build_model()
        self.assertIsNotNone(str(b))

    def test_fw_str(self):
        fw = self._create_firmware_image()
        self.assertIn(str(fw.build), str(fw))
        self.assertIn(fw.file.name, str(fw))

    def test_fw_str_new(self):
        fw = self.firmware_image_model()
        self.assertIsNotNone(str(fw))

    def test_fw_auto_type(self):
        fw = self._create_firmware_image(type='')
        self.assertEqual(fw.type, self.TPLINK_4300_IMAGE)

    def test_device_firmware_image_invalid_org(self):
        device_fw = self._create_device_firmware()
        self._create_org(name='org2')
        img2 = self._create_firmware_image()
        device_fw.image = img2
        try:
            device_fw.full_clean()
        except ValidationError as e:
            self.assertIn('image', e.message_dict)
        else:
            self.fail('ValidationError not raised')

    def test_device_fw_image_changed(self, *args):
        with mock.patch(f'{self.app_name}.models.UpgradeOperation.upgrade', return_value=None):
            device_fw = self.device_firmware_model()
            self.assertIsNone(device_fw._old_image)
            # save
            device_fw = self._create_device_firmware(upgrade=False)
            self.assertEqual(device_fw._old_image, device_fw.image)
            self.assertEqual(self.upgrade_operation_model.objects.count(), 0)
            # init
            device_fw = self.device_firmware_model.objects.first()
            self.assertEqual(device_fw._old_image, device_fw.image)
            # change
            build2 = self._create_build(category=device_fw.image.build.category,
                                        version='0.2')
            fw2 = self._create_firmware_image(build=build2, type=device_fw.image.type)
            old_image = device_fw.image
            device_fw.image = fw2
            self.assertNotEqual(device_fw._old_image, device_fw.image)
            self.assertEqual(device_fw._old_image, old_image)
            device_fw.full_clean()
            device_fw.save()
            self.assertEqual(self.upgrade_operation_model.objects.count(), 1)
            self.assertEqual(self.batch_upgrade_operation_model.objects.count(), 0)

    def test_device_fw_created(self, *args):
        with mock.patch(f'{self.app_name}.models.UpgradeOperation.upgrade', return_value=None):
            self._create_device_firmware(upgrade=True)
            self.assertEqual(self.upgrade_operation_model.objects.count(), 1)
            self.assertEqual(self.batch_upgrade_operation_model.objects.count(), 0)

    def test_device_fw_image_saved_not_installed(self, *args):
        with mock.patch(f'{self.app_name}.models.UpgradeOperation.upgrade', return_value=None):
            device_fw = self.device_firmware_model()
            self.assertIsNone(device_fw._old_image)
            # save
            device_fw = self._create_device_firmware(upgrade=False, installed=False)
            self.assertEqual(device_fw._old_image, device_fw.image)
            self.assertEqual(self.upgrade_operation_model.objects.count(), 0)
            device_fw.full_clean()
            device_fw.save()
            self.assertEqual(self.upgrade_operation_model.objects.count(), 1)
            self.assertEqual(self.batch_upgrade_operation_model.objects.count(), 0)

    def test_device_fw_no_connection(self):
        try:
            self._create_device_firmware(device_connection=False)
        except ValidationError as e:
            self.assertIn('related connection', str(e))
        else:
            self.fail('ValidationError not raised')

    def test_invalid_board(self):
        image = FIRMWARE_IMAGE_MAP['ar71xx-generic-tl-wdr4300-v1-squashfs-sysupgrade.bin']
        boards = image['boards']
        del image['boards']
        err = None
        try:
            self._create_firmware_image()
        except ValidationError as e:
            err = e
        image['boards'] = boards
        if err:
            self.assertIn('type', err.message_dict)
            self.assertIn('not find boards', str(err))
        else:
            self.fail('ValidationError not raised')

    def test_custom_image_type_present(self):
        t = self.firmware_image_model._meta.get_field('type')
        custom_images = app_settings.CUSTOM_OPENWRT_IMAGES
        self.assertEqual(t.choices[0][0], custom_images[0][0])

    def test_device_firmware_image_invalid_model(self):
        device_fw = self._create_device_firmware()
        different_img = self._create_firmware_image(
            build=device_fw.image.build,
            type=self.TPLINK_4300_IL_IMAGE
        )
        try:
            device_fw.image = different_img
            device_fw.full_clean()
        except ValidationError as e:
            self.assertIn('model do not match', str(e))
        else:
            self.fail('ValidationError not raised')


class BaseTestModelsTransaction(object):
    _mock_updrade = 'openwisp_firmware_upgrader.upgraders.openwrt.OpenWrt.upgrade'
    _mock_connect = 'openwisp_controller.connection.models.DeviceConnection.connect'

    @mock.patch(_mock_updrade, return_value=True)
    @mock.patch(_mock_connect, return_value=True)
    def test_upgrade_related_devices(self, *args):
        env = self._create_upgrade_env()
        # check everything is as expected
        self.assertEqual(self.upgrade_operation_model.objects.count(), 0)
        self.assertEqual(env['d1'].devicefirmware.image, env['image1a'])
        self.assertEqual(env['d2'].devicefirmware.image, env['image1b'])
        # upgrade all related
        env['build2'].batch_upgrade(firmwareless=False)
        # ensure image is changed
        env['d1'].devicefirmware.refresh_from_db()
        env['d2'].devicefirmware.refresh_from_db()
        self.assertEqual(env['d1'].devicefirmware.image, env['image2a'])
        self.assertEqual(env['d2'].devicefirmware.image, env['image2b'])
        # ensure upgrade operation objects have been created
        self.assertEqual(self.upgrade_operation_model.objects.count(), 2)
        batch_qs = self.batch_upgrade_operation_model.objects.all()
        self.assertEqual(batch_qs.count(), 1)
        batch = batch_qs.first()
        self.assertEqual(batch.upgradeoperation_set.count(), 2)
        self.assertEqual(batch.build, env['build2'])
        self.assertEqual(batch.status, 'success')

    @mock.patch(_mock_updrade, return_value=True)
    @mock.patch(_mock_connect, return_value=True)
    def test_upgrade_firmwareless_devices(self, *args):
        env = self._create_upgrade_env(device_firmware=False)
        # check everything is as expected
        self.assertEqual(self.upgrade_operation_model.objects.count(), 0)
        self.assertFalse(hasattr(env['d1'], 'devicefirmware'))
        self.assertFalse(hasattr(env['d2'], 'devicefirmware'))
        # upgrade all related
        env['build2'].batch_upgrade(firmwareless=True)
        env['d1'].refresh_from_db()
        env['d2'].refresh_from_db()
        self.assertEqual(env['d1'].devicefirmware.image, env['image2a'])
        self.assertEqual(env['d2'].devicefirmware.image, env['image2b'])
        # ensure upgrade operation objects have been created
        self.assertEqual(self.upgrade_operation_model.objects.count(), 2)
        batch_qs = self.batch_upgrade_operation_model.objects.all()
        self.assertEqual(batch_qs.count(), 1)
        batch = batch_qs.first()
        self.assertEqual(batch.upgradeoperation_set.count(), 2)
        self.assertEqual(batch.build, env['build2'])
        self.assertEqual(batch.status, 'success')

    def test_batch_upgrade_failure(self):
        env = self._create_upgrade_env()
        env['build2'].batch_upgrade(firmwareless=False)
        batch = self.batch_upgrade_operation_model.objects.first()
        self.assertEqual(batch.status, 'failed')

    @mock.patch(_mock_updrade, return_value=True)
    @mock.patch(_mock_connect, return_value=True)
    def test_upgrade_related_devices_existing_fw(self, *args):
        env = self._create_upgrade_env()
        self.assertEqual(self.upgrade_operation_model.objects.count(), 0)
        self.assertEqual(env['d1'].devicefirmware.image, env['image1a'])
        self.assertEqual(env['d2'].devicefirmware.image, env['image1b'])
        env['d1'].devicefirmware.installed = False
        env['d1'].devicefirmware.save(upgrade=False)
        env['d2'].devicefirmware.installed = False
        env['d2'].devicefirmware.save(upgrade=False)
        env['build1'].batch_upgrade(firmwareless=False)
        env['d1'].devicefirmware.refresh_from_db()
        env['d2'].devicefirmware.refresh_from_db()
        self.assertEqual(env['d1'].devicefirmware.image, env['image1a'])
        self.assertEqual(env['d2'].devicefirmware.image, env['image1b'])
        self.assertEqual(self.upgrade_operation_model.objects.count(), 2)
        batch_qs = self.batch_upgrade_operation_model.objects.all()
        self.assertEqual(batch_qs.count(), 1)
        batch = batch_qs.first()
        self.assertEqual(batch.upgradeoperation_set.count(), 2)
        self.assertEqual(batch.build, env['build1'])
        self.assertEqual(batch.status, 'success')
