// SPDX-License-Identifier: GPL-2.0-only
/*
 * drv-lab platform device injector.
 *
 * Publishes a platform device with a chosen name, so that a driver whose only
 * binding path is a device tree compatible string still gets a probe().
 *
 * Why this exists
 * ---------------
 * A platform driver that declares only .of_match_table cannot bind without a
 * device tree node, and a QEMU arm64 virt guest offers no way to gain one:
 *
 *   - passing -dtb suppresses the /chosen entries QEMU would otherwise add, so
 *     the guest boots with no command line and no initramfs at all;
 *   - CONFIG_OF_CONFIGFS is not mainline, so /sys/kernel/config/device-tree/
 *     overlays does not exist;
 *   - of_overlay_fdt_apply() would work, but it needs CONFIG_OF_OVERLAY, which
 *     a stock guest config does not set.
 *
 * What is always available is platform_match()'s last resort:
 *
 *     return (strcmp(pdev->name, drv->name) == 0);
 *
 * A platform device named after the driver binds it, with no device tree at all.
 *
 * What this does NOT do
 * ---------------------
 * It never exercises the driver's of_match_table, so a wrong compatible string
 * is not caught. A driver that reads properties out of its device node in
 * probe() will also see none. Establishing either needs a real node, which
 * means CONFIG_OF_OVERLAY and of_overlay_fdt_apply().
 *
 * Usage
 * -----
 *     insmod drvlab-injector.ko name=demo_misc
 */

#include <linux/module.h>
#include <linux/platform_device.h>

static char *name;
module_param(name, charp, 0444);
MODULE_PARM_DESC(name, "platform device name to publish; must equal the driver's .name");

static struct platform_device *injected;

static int __init drvlab_injector_init(void)
{
	if (!name || !*name) {
		pr_err("drvlab-injector: name= is required\n");
		return -EINVAL;
	}

	injected = platform_device_register_simple(name, PLATFORM_DEVID_NONE, NULL, 0);
	if (IS_ERR(injected))
		return PTR_ERR(injected);

	pr_info("drvlab-injector: published platform device '%s'\n", name);
	return 0;
}

static void __exit drvlab_injector_exit(void)
{
	platform_device_unregister(injected);
}

module_init(drvlab_injector_init);
module_exit(drvlab_injector_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("drv-lab: publish a platform device so a device-tree-only driver can probe");
