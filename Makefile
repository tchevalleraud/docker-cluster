VERSION ?= dev
ACCEL   ?= kvm

.PHONY: all test image ova clean

all: ova

test:
	python3 -W ignore::DeprecationWarning -m unittest discover tests -v

image:
	cd packer && packer init . && packer build -force \
		-var accelerator=$(ACCEL) -var appliance_version=$(VERSION) \
		-var output_dir=../output/qemu .

ova: image
	ova/build-ova.sh output/qemu/system.qcow2 output $(VERSION)

clean:
	rm -rf output packer/packer_cache
