packer {
  required_plugins {
    qemu = {
      source  = "github.com/hashicorp/qemu"
      version = ">= 1.1.0"
    }
  }
}

variable "base_image_url" {
  type        = string
  description = "Ubuntu 24.04 minimal cloud image (qcow2)."
  default     = "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img"
}

variable "base_image_checksum" {
  type        = string
  description = "Checksum of the base image, or a file: URL to a SHA256SUMS list."
  default     = "file:https://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS"
}

variable "disk_size" {
  type        = string
  description = "Size of the system disk."
  default     = "20G"
}

variable "accelerator" {
  type        = string
  description = "QEMU accelerator: kvm when available, tcg otherwise (much slower)."
  default     = "kvm"
}

variable "output_dir" {
  type    = string
  default = "../output/qemu"
}

variable "appliance_version" {
  type    = string
  default = "dev"
}

source "qemu" "appliance" {
  iso_url      = var.base_image_url
  iso_checksum = var.base_image_checksum
  disk_image   = true
  disk_size    = var.disk_size
  format       = "qcow2"

  accelerator    = var.accelerator
  cpus           = 2
  memory         = 2048
  headless       = true
  disk_interface = "virtio"
  net_device     = "virtio-net"

  # cloud-init NoCloud seed: creates the temporary "packer" build user.
  cd_label = "cidata"
  cd_files = ["./cloud-init/meta-data", "./cloud-init/user-data"]

  ssh_username = "packer"
  ssh_password = "packer"
  ssh_timeout  = "60m"

  # The build user deletes itself on the way out.
  shutdown_command = "echo packer | sudo -S sh -c 'userdel -rf packer; rm -f /etc/sudoers.d/90-cloud-init-users /etc/sudoers.d/packer; shutdown -P now'"

  output_directory = var.output_dir
  vm_name          = "system.qcow2"
}

build {
  sources = ["source.qemu.appliance"]

  # Wait for cloud-init to finish before touching apt.
  provisioner "shell" {
    inline = ["cloud-init status --wait || true"]
  }

  # Files that ship as-is inside the appliance.
  provisioner "file" {
    source      = "../rootfs"
    destination = "/tmp"
  }

  provisioner "shell" {
    execute_command = "echo packer | sudo -S env {{ .Vars }} bash -eux '{{ .Path }}'"
    environment_vars = [
      "APPLIANCE_VERSION=${var.appliance_version}",
      "DEBIAN_FRONTEND=noninteractive",
    ]
    scripts = [
      "./scripts/10-base.sh",
      "./scripts/20-docker.sh",
      "./scripts/30-appliance.sh",
      "./scripts/90-cleanup.sh",
    ]
  }
}
