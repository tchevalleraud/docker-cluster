"""Tests for the first-boot logic (run with: python3 -m unittest discover tests)."""

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "rootfs", "usr", "local", "lib", "dc-appliance", "appliance.py")

OVF_ENV = """<?xml version="1.0" encoding="UTF-8"?>
<Environment xmlns="http://schemas.dmtf.org/ovf/environment/1"
     xmlns:oe="http://schemas.dmtf.org/ovf/environment/1"
     xmlns:ve="http://www.vmware.com/schema/ovfenv"
     oe:id="" ve:vCenterId="vm-1">
   <PlatformSection><Kind>VMware ESXi</Kind><Version>6.5.0</Version></PlatformSection>
   <PropertySection>
         <Property oe:key="appliance.dns" oe:value="192.168.1.1, 1.1.1.1"/>
         <Property oe:key="appliance.domain" oe:value="lab.local"/>
         <Property oe:key="appliance.gateway" oe:value="192.168.1.1"/>
         <Property oe:key="appliance.hostname" oe:value="node1"/>
         <Property oe:key="appliance.ip" oe:value="192.168.1.10"/>
         <Property oe:key="appliance.keyboard" oe:value="fr"/>
         <Property oe:key="appliance.ntp" oe:value=""/>
         <Property oe:key="appliance.password" oe:value="s3cretpass"/>
         <Property oe:key="appliance.prefix" oe:value="24"/>
         <Property oe:key="appliance.ssh" oe:value="True"/>
   </PropertySection>
   <ve:EthernetAdapterSection>
      <ve:Adapter ve:mac="00:50:56:aa:bb:cc" ve:network="VM Network" ve:unitNumber="7"/>
   </ve:EthernetAdapterSection>
</Environment>
"""


def load_module(root):
    os.environ["DC_ROOT"] = root
    spec = importlib.util.spec_from_file_location("appliance", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ApplianceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        net = os.path.join(self.root, "sys/class/net")
        for name, mac in (("lo", "00:00:00:00:00:00"), ("ens192", "00:50:56:aa:bb:cc")):
            os.makedirs(os.path.join(net, name))
            with open(os.path.join(net, name, "address"), "w") as f:
                f.write(mac + "\n")
        os.makedirs(os.path.join(net, "ens192", "device"))
        os.makedirs(os.path.join(self.root, "etc/netplan"))
        with open(os.path.join(self.root, "etc/netplan/00-fallback.yaml"), "w") as f:
            f.write("fallback\n")
        self.m = load_module(self.root)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DC_ROOT", None)
        os.environ.pop("DC_OVFENV_FILE", None)

    def read(self, p):
        with open(os.path.join(self.root, p.lstrip("/"))) as f:
            return f.read()

    def test_parse_ovf_env(self):
        props = self.m.parse_ovf_env(OVF_ENV)
        self.assertEqual(props["appliance.ip"], "192.168.1.10")
        self.assertEqual(props["appliance.ssh"], "True")
        self.assertEqual(self.m.parse_ovf_env(""), {})

    def test_settings_from_ovf(self):
        settings, password = self.m.settings_from_ovf(self.m.parse_ovf_env(OVF_ENV))
        s = self.m.validate(settings)
        self.assertEqual(password, "s3cretpass")
        self.assertEqual(s["prefix"], 24)
        self.assertEqual(s["dns"], ["192.168.1.1", "1.1.1.1"])
        self.assertEqual(s["ntp"], [])
        self.assertTrue(s["ssh"])
        self.assertEqual(s["keyboard"], "fr")

    def base(self, **kw):
        s = {"hostname": "node1", "ip": "10.0.0.5", "prefix": "24", "gateway": "10.0.0.1"}
        s.update(kw)
        return s

    def test_validate_accepts_cidr_in_ip_field(self):
        s = self.m.validate(self.base(ip="10.0.0.5/16", prefix=""))
        self.assertEqual((s["ip"], s["prefix"]), ("10.0.0.5", 16))

    def test_validate_rejects_bad_values(self):
        bad = [
            self.base(hostname="-bad"),
            self.base(hostname="has space"),
            self.base(ip="10.0.0.300"),
            self.base(ip="10.0.0.0"),
            self.base(gateway="192.168.0.1"),
            self.base(gateway="10.0.0.5"),
            self.base(dns="10.0.0.1, nope"),
            self.base(keyboard="azerty"),
            self.base(prefix="33"),
        ]
        for s in bad:
            with self.assertRaises(ValueError, msg=s):
                self.m.validate(s)

    def test_render_netplan(self):
        s = self.m.validate(self.base(dns="10.0.0.1", domain="lab.local"))
        out = self.m.render_netplan(s, "ens192", "00:50:56:aa:bb:cc")
        self.assertIn('addresses: ["10.0.0.5/24"]', out)
        self.assertIn("via: 10.0.0.1", out)
        self.assertIn("search: [lab.local]", out)
        self.assertIn('macaddress: "00:50:56:aa:bb:cc"', out)

    def test_firstboot_applies_ovf(self):
        envfile = os.path.join(self.root, "ovfenv.xml")
        with open(envfile, "w") as f:
            f.write(OVF_ENV)
        os.environ["DC_OVFENV_FILE"] = envfile
        self.assertEqual(self.m.cmd_firstboot(), 0)
        self.assertIn("ens192:", self.read("/etc/netplan/50-appliance.yaml"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/netplan/00-fallback.yaml")))
        self.assertEqual(self.read("/etc/hostname"), "node1\n")
        self.assertIn("192.168.1.10 node1.lab.local node1", self.read("/etc/hosts"))
        self.assertIn('XKBLAYOUT="fr"', self.read("/etc/default/keyboard"))
        saved = json.loads(self.read("/etc/dc-appliance/appliance.json"))
        self.assertEqual(saved["gateway"], "192.168.1.1")
        # The password is set by chpasswd on a real system, so in tests the
        # appliance is not marked as configured yet.
        self.assertFalse(self.m.is_configured())

    def test_firstboot_without_properties_keeps_dhcp(self):
        envfile = os.path.join(self.root, "empty.xml")
        open(envfile, "w").close()
        os.environ["DC_OVFENV_FILE"] = envfile
        self.assertEqual(self.m.cmd_firstboot(), 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, "etc/netplan/00-fallback.yaml")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/netplan/50-appliance.yaml")))

    def test_firstboot_with_invalid_properties_keeps_dhcp(self):
        envfile = os.path.join(self.root, "bad.xml")
        with open(envfile, "w") as f:
            f.write(OVF_ENV.replace('oe:value="192.168.1.1"/>', 'oe:value="8.8.8.8"/>'))
        os.environ["DC_OVFENV_FILE"] = envfile
        self.assertEqual(self.m.cmd_firstboot(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/netplan/50-appliance.yaml")))


if __name__ == "__main__":
    unittest.main()
