#!/usr/bin/env python3
"""Docker Cluster appliance: first-boot configuration and VM console.

Subcommands:
  firstboot   Read the OVF properties given at deployment (VMware guestinfo)
              and configure hostname, static network, NTP, keyboard, admin
              password and SSH. Without usable properties, the VM stays on
              DHCP and the console asks for the settings.
  console     Text menu shown on the VM screen (tty1).
  show        Print the current settings (JSON).

Only the Python standard library is used, so the script runs on the minimal
Ubuntu image without extra packages.
"""

import getpass
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

try:  # Deprecated but still shipped with Python 3.12 (Ubuntu 24.04).
    import crypt
except ImportError:  # pragma: no cover
    crypt = None

ROOT = os.environ.get("DC_ROOT", "/")
PRODUCT = "Docker Cluster appliance"
OVF_PREFIX = "appliance."
KEYBOARD_LAYOUTS = ("us", "fr", "be", "ch", "de", "gb", "es", "it")


def path(p):
    return os.path.join(ROOT, p.lstrip("/"))


CONFIG_FILE = "/etc/dc-appliance/appliance.json"
VERSION_FILE = "/etc/dc-appliance/version"
CONFIGURED_MARK = "/var/lib/dc-appliance/configured"
NETPLAN_FILE = "/etc/netplan/50-appliance.yaml"
FALLBACK_NETPLAN = "/etc/netplan/00-fallback.yaml"
TIMESYNCD_FILE = "/etc/systemd/timesyncd.conf.d/appliance.conf"
KEYBOARD_FILE = "/etc/default/keyboard"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
                       r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def split_list(value):
    return [v for v in re.split(r"[\s,;]+", value or "") if v]


def parse_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def validate(settings):
    """Check and normalise settings. Raises ValueError with a readable message."""
    s = dict(settings)
    if not HOSTNAME_RE.match(s.get("hostname", "")):
        raise ValueError("invalid hostname: %r" % s.get("hostname"))
    if s.get("domain") and not DOMAIN_RE.match(s["domain"]):
        raise ValueError("invalid domain: %r" % s["domain"])

    ip_text = s.get("ip", "").strip()
    prefix = str(s.get("prefix", "")).strip()
    if "/" in ip_text:  # Accept "192.168.1.10/24" in the IP field.
        ip_text, prefix = ip_text.split("/", 1)
    try:
        iface = ipaddress.IPv4Interface("%s/%s" % (ip_text, prefix))
    except ValueError:
        raise ValueError("invalid IP address or prefix: %s/%s" % (ip_text, prefix))
    if iface.ip in (iface.network.network_address, iface.network.broadcast_address) \
            and iface.network.prefixlen < 31:
        raise ValueError("%s is not a usable host address in %s" % (iface.ip, iface.network))
    s["ip"] = str(iface.ip)
    s["prefix"] = iface.network.prefixlen

    try:
        gw = ipaddress.IPv4Address(s.get("gateway", "").strip())
    except ValueError:
        raise ValueError("invalid gateway: %r" % s.get("gateway"))
    if gw not in iface.network:
        raise ValueError("gateway %s is not in %s" % (gw, iface.network))
    if gw == iface.ip:
        raise ValueError("gateway cannot be the appliance address")
    s["gateway"] = str(gw)

    dns = s.get("dns", [])
    dns = split_list(dns) if isinstance(dns, str) else list(dns)
    for d in dns:
        try:
            ipaddress.ip_address(d)
        except ValueError:
            raise ValueError("invalid DNS server: %r" % d)
    s["dns"] = dns

    ntp = s.get("ntp", [])
    s["ntp"] = split_list(ntp) if isinstance(ntp, str) else list(ntp)

    kb = (s.get("keyboard") or "us").strip().lower()
    if kb not in KEYBOARD_LAYOUTS:
        raise ValueError("unsupported keyboard layout: %r (use one of %s)"
                         % (kb, ", ".join(KEYBOARD_LAYOUTS)))
    s["keyboard"] = kb
    s["ssh"] = parse_bool(s.get("ssh", False))
    return s


def load_settings():
    try:
        with open(path(CONFIG_FILE)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    os.makedirs(os.path.dirname(path(CONFIG_FILE)), exist_ok=True)
    write_file(CONFIG_FILE, json.dumps(settings, indent=2, sort_keys=True) + "\n", 0o644)


# --------------------------------------------------------------------------
# OVF environment (VMware guestinfo transport)
# --------------------------------------------------------------------------

OE_NS = "http://schemas.dmtf.org/ovf/environment/1"


def read_ovf_env():
    """Return the raw OVF environment XML, or '' when there is none."""
    test_file = os.environ.get("DC_OVFENV_FILE")
    if test_file:
        with open(test_file) as f:
            return f.read()
    for cmd in (["vmware-rpctool", "info-get guestinfo.ovfEnv"],
                ["vmtoolsd", "--cmd", "info-get guestinfo.ovfEnv"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    return ""


def parse_ovf_env(xml_text):
    """Extract {key: value} from the PropertySection of an OVF environment."""
    if not xml_text.strip():
        return {}
    root = ET.fromstring(xml_text)
    props = {}
    for prop in root.iter("{%s}Property" % OE_NS):
        key = prop.get("{%s}key" % OE_NS)
        value = prop.get("{%s}value" % OE_NS, "")
        if key:
            props[key] = value
    return props


def settings_from_ovf(props):
    """Map OVF properties to (settings, password)."""
    def get(name, default=""):
        return props.get(OVF_PREFIX + name, default).strip()

    settings = {
        "hostname": get("hostname"),
        "domain": get("domain"),
        "ip": get("ip"),
        "prefix": get("prefix", "24"),
        "gateway": get("gateway"),
        "dns": get("dns"),
        "ntp": get("ntp"),
        "keyboard": get("keyboard", "us"),
        "ssh": get("ssh", "false"),
    }
    return settings, props.get(OVF_PREFIX + "password", "")


# --------------------------------------------------------------------------
# System changes
# --------------------------------------------------------------------------

def write_file(p, content, mode=0o644):
    full = path(p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.chmod(tmp, mode)
    os.replace(tmp, full)


def run(*cmd, check=True):
    """Run a command quietly (its output would clutter the VM console)."""
    if ROOT != "/":  # Test mode: never touch the host.
        return
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if check and out.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd), out.stdout.strip()[-300:]))


def primary_interface():
    """Name and MAC of the first physical Ethernet interface."""
    base = path("/sys/class/net")
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if name == "lo" or not os.path.exists(os.path.join(base, name, "device")):
            continue
        try:
            with open(os.path.join(base, name, "address")) as f:
                return name, f.read().strip()
        except OSError:
            continue
    return None, None


def render_netplan(settings, iface, mac):
    lines = [
        "# Managed by the Docker Cluster appliance. Change it from the console",
        "# or the management UI: manual edits are overwritten.",
        "network:",
        "  version: 2",
        "  renderer: networkd",
        "  ethernets:",
        "    %s:" % iface,
    ]
    if mac:
        lines += ["      match:", "        macaddress: \"%s\"" % mac,
                  "      set-name: %s" % iface]
    lines += [
        "      dhcp4: false",
        "      dhcp6: false",
        "      accept-ra: false",
        "      addresses: [\"%s/%s\"]" % (settings["ip"], settings["prefix"]),
        "      routes:",
        "        - to: default",
        "          via: %s" % settings["gateway"],
    ]
    if settings["dns"] or settings.get("domain"):
        lines.append("      nameservers:")
        if settings["dns"]:
            lines.append("        addresses: [%s]" % ", ".join(settings["dns"]))
        if settings.get("domain"):
            lines.append("        search: [%s]" % settings["domain"])
    return "\n".join(lines) + "\n"


def render_hosts(settings):
    fqdn = settings["hostname"]
    names = settings["hostname"]
    if settings.get("domain"):
        fqdn = "%s.%s" % (settings["hostname"], settings["domain"])
        names = "%s %s" % (fqdn, settings["hostname"])
    return ("127.0.0.1 localhost\n"
            "%s %s\n\n"
            "::1 localhost ip6-localhost ip6-loopback\n"
            "ff02::1 ip6-allnodes\n"
            "ff02::2 ip6-allrouters\n") % (settings["ip"], names)


def apply_settings(settings):
    iface, mac = primary_interface()
    if not iface:
        raise RuntimeError("no Ethernet interface found")

    write_file(NETPLAN_FILE, render_netplan(settings, iface, mac), 0o600)
    if os.path.exists(path(FALLBACK_NETPLAN)):
        os.remove(path(FALLBACK_NETPLAN))

    write_file("/etc/hostname", settings["hostname"] + "\n")
    write_file("/etc/hosts", render_hosts(settings))
    run("hostnamectl", "set-hostname", settings["hostname"], check=False)

    if settings["ntp"]:
        write_file(TIMESYNCD_FILE, "[Time]\nNTP=%s\n" % " ".join(settings["ntp"]))
    elif os.path.exists(path(TIMESYNCD_FILE)):
        os.remove(path(TIMESYNCD_FILE))
    run("systemctl", "restart", "systemd-timesyncd.service", check=False)

    apply_keyboard(settings["keyboard"])
    apply_ssh(settings["ssh"])
    run("netplan", "apply")
    save_settings(settings)


def current_keyboard():
    try:
        with open(path(KEYBOARD_FILE)) as f:
            m = re.search(r'^XKBLAYOUT="?([a-z]+)', f.read(), re.M)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "us"


def apply_keyboard(layout):
    write_file(KEYBOARD_FILE,
               'XKBMODEL="pc105"\nXKBLAYOUT="%s"\nXKBVARIANT=""\nXKBOPTIONS=""\n'
               'BACKSPACE="guess"\n' % layout)
    run("setupcon", "--force", "--save", check=False)


def apply_ssh(enabled):
    if enabled:
        run("ssh-keygen", "-A")
        run("systemctl", "enable", "--now", "ssh.socket", check=False)
    else:
        run("systemctl", "disable", "--now", "ssh.socket", "ssh.service", check=False)


def set_password(password):
    if ROOT != "/":
        return
    subprocess.run(["chpasswd"], input="admin:%s\n" % password, text=True, check=True)
    subprocess.run(["passwd", "-u", "admin"], check=False, capture_output=True)


def admin_hash():
    try:
        with open(path("/etc/shadow")) as f:
            for line in f:
                fields = line.split(":")
                if fields[0] == "admin":
                    return fields[1]
    except OSError:
        pass
    return ""


def admin_password_set():
    h = admin_hash()
    return bool(h) and not h.startswith(("!", "*"))


def check_password(password):
    h = admin_hash()
    if not h or h.startswith(("!", "*")) or crypt is None:
        return False
    return crypt.crypt(password, h) == h


def mark_configured():
    write_file(CONFIGURED_MARK, time.strftime("%Y-%m-%dT%H:%M:%S%z\n"))


def is_configured():
    return os.path.exists(path(CONFIGURED_MARK))


# --------------------------------------------------------------------------
# firstboot
# --------------------------------------------------------------------------

def log(msg):
    print("dc-appliance: %s" % msg, flush=True)


def cmd_firstboot():
    run("ssh-keygen", "-A", check=False)  # Fresh host keys for this VM.

    try:
        props = parse_ovf_env(read_ovf_env())
    except ET.ParseError as e:
        log("unreadable OVF environment (%s)" % e)
        props = {}

    if not props:
        log("no OVF properties found: staying on DHCP, finish the setup on the VM console")
        apply_ssh(False)
        return 0

    settings, password = settings_from_ovf(props)
    try:
        settings = validate(settings)
    except ValueError as e:
        log("OVF properties rejected (%s): finish the setup on the VM console" % e)
        apply_ssh(False)
        return 0

    apply_settings(settings)
    if password:
        set_password(password)
    if admin_password_set():
        mark_configured()
        log("configured %s with %s/%s" % (settings["hostname"], settings["ip"], settings["prefix"]))
    else:
        log("network configured; set the admin password on the VM console")
    return 0


# --------------------------------------------------------------------------
# console
# --------------------------------------------------------------------------

def current_ipv4():
    """IPv4 addresses of the management interface (not Docker bridges)."""
    iface, _ = primary_interface()
    if not iface:
        return []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", iface, "scope", "global"],
                             capture_output=True, text=True).stdout
    except OSError:
        return []
    return re.findall(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out)


def read_version():
    try:
        with open(path(VERSION_FILE)) as f:
            return f.read().strip()
    except OSError:
        return "dev"


def clear():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def banner():
    clear()
    settings = load_settings()
    addrs = current_ipv4()
    hostname = os.uname().nodename
    print("=" * 72)
    print("  %s  -  version %s" % (PRODUCT, read_version()))
    print("=" * 72)
    print()
    print("  Hostname : %s" % hostname)
    print("  Address  : %s" % (", ".join(addrs) if addrs else "none"))
    if settings:
        print("  Gateway  : %s" % settings.get("gateway", "-"))
        print("  DNS      : %s" % (", ".join(settings.get("dns", [])) or "-"))
        print("  SSH      : %s" % ("enabled" if settings.get("ssh") else "disabled"))
    if addrs:
        print()
        print("  Management : https://%s" % addrs[0].split("/")[0])
    print()
    if not is_configured():
        print("  This appliance is not configured yet.")
        print()


def flush_input():
    """Drop keys typed while the appliance was busy (they would be echoed)."""
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def ask(label, default=""):
    suffix = " [%s]" % default if default else ""
    value = input("  %s%s: " % (label, suffix)).strip()
    return value or default


def ask_password(confirm=True):
    flush_input()
    while True:
        pw = getpass.getpass("  New admin password: ")
        if len(pw) < 8:
            print("  The password must have at least 8 characters.")
            continue
        if confirm and getpass.getpass("  Confirm password: ") != pw:
            print("  The passwords do not match.")
            continue
        return pw


def pause(msg="Press Enter to continue."):
    input("  %s" % msg)


def network_wizard():
    current = load_settings()
    print()
    print("  Network settings (Ctrl+C to cancel)")
    print()
    active_kb = current_keyboard()
    kb = ask("Keyboard layout (%s)" % "/".join(KEYBOARD_LAYOUTS), active_kb)
    if kb not in KEYBOARD_LAYOUTS:
        kb = active_kb
    if kb != active_kb:
        print("  Switching keyboard layout...")
        apply_keyboard(kb)
    while True:
        s = {
            "keyboard": kb,
            "hostname": ask("Hostname", current.get("hostname", "docker-node")),
            "domain": ask("Domain (optional)", current.get("domain", "")),
            "ip": ask("IPv4 address", current.get("ip", "")),
            "prefix": ask("Prefix length", str(current.get("prefix", 24))),
            "gateway": ask("Gateway", current.get("gateway", "")),
            "dns": ask("DNS servers (comma separated)", ", ".join(current.get("dns", []))),
            "ntp": ask("NTP servers (optional)", ", ".join(current.get("ntp", []))),
            "ssh": current.get("ssh", False),
        }
        try:
            s = validate(s)
            break
        except ValueError as e:
            print()
            print("  Error: %s" % e)
            print()
            current.update({k: v for k, v in s.items() if v})
    print()
    print("  %s/%s via %s, hostname %s" % (s["ip"], s["prefix"], s["gateway"], s["hostname"]))
    if ask("Apply these settings? (yes/no)", "yes").lower() not in ("y", "yes"):
        return
    apply_settings(s)
    print("  Settings applied.")


def toggle_ssh():
    s = load_settings()
    if not s:
        print("  Configure the network first.")
        return
    s["ssh"] = not s.get("ssh", False)
    apply_ssh(s["ssh"])
    save_settings(s)
    print("  SSH is now %s." % ("enabled" if s["ssh"] else "disabled"))


def setup_flow():
    """First configuration from the console: no login needed yet."""
    network_wizard()
    if not admin_password_set():
        print()
        set_password(ask_password())
    if load_settings() and admin_password_set():
        mark_configured()
        print()
        print("  The appliance is configured.")
    pause()


def menu():
    while True:
        banner()
        print("  1) Network settings")
        print("  2) Change admin password")
        print("  3) Enable/disable SSH")
        print("  4) Restart")
        print("  5) Shut down")
        print("  0) Log out")
        print()
        choice = input("  Choice: ").strip()
        if choice == "1":
            network_wizard()
            pause()
        elif choice == "2":
            set_password(ask_password())
            print("  Password changed.")
            pause()
        elif choice == "3":
            toggle_ssh()
            pause()
        elif choice == "4":
            if ask("Restart the appliance? (yes/no)", "no").lower() in ("y", "yes"):
                run("systemctl", "reboot")
                time.sleep(30)
        elif choice == "5":
            if ask("Shut down the appliance? (yes/no)", "no").lower() in ("y", "yes"):
                run("systemctl", "poweroff")
                time.sleep(30)
        elif choice == "0":
            return


def cmd_console():
    while True:
        try:
            banner()
            if not is_configured():
                pause("Press Enter to configure the appliance.")
                setup_flow()
                continue
            pause("Press Enter to log in.")
            flush_input()
            pw = getpass.getpass("  admin password: ")
            if not check_password(pw):
                print("  Wrong password.")
                time.sleep(3)
                continue
            menu()
        except KeyboardInterrupt:
            print()
        except EOFError:
            time.sleep(1)
        except Exception as e:  # Keep the console alive whatever happens.
            print("\n  Error: %s" % e)
            time.sleep(5)


def main(argv):
    if len(argv) < 2 or argv[1] not in ("firstboot", "console", "show"):
        print(__doc__)
        return 2
    if argv[1] == "firstboot":
        return cmd_firstboot()
    if argv[1] == "console":
        return cmd_console()
    print(json.dumps(load_settings(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
