# OpenWrt for the GL.iNet Slate 7 (GL-BE3600)

OpenWrt firmware for the **GL.iNet Slate 7 (GL-BE3600, BE3600)** Wi-Fi 7
travel router: Qualcomm **IPQ5332** (quad Cortex-A53), dual-band Wi-Fi 7, two
2.5 GbE ports, USB 3.0, and the side touch screen.

Complete, buildable OpenWrt tree.

Based on [perceival/openwrt-flint3](https://github.com/perceival/openwrt-flint3)
(`flint3-be9300` at `b12c854`, target `qualcommbe/ipq53xx`, kernel 6.18), with
the package feeds pinned to the revisions the tested images were built from.

Two images: a stock OpenWrt image and an extended one with USB modem,
tethering, storage, file sharing and VPN support. Prebuilt images are on the
[Releases](https://github.com/fivefold3/openwrt-gl-be3600/releases) page.

Nothing here is in official OpenWrt: the `ipq53xx` subtarget itself is not
upstream yet, and the 5 GHz radio depends on an out-of-tree driver patch.

> [!WARNING]
> **Unofficial, community port. Not affiliated with, endorsed by or supported
> by GL.iNet or the OpenWrt project.** Provided as-is, with no warranty of any
> kind. Flashing third-party firmware can brick the device and may void the
> warranty. **Back up the NAND first** (`scripts/gl-be3600/backup-mtd.sh`):
> the `0:ART` partition holds the unit's radio calibration and MAC addresses
> and exists nowhere else. Developed and tested on a single unit.

## Status

| Area | State |
|---|---|
| Boot / install | Works. GL-format factory image (accepted by the stock firmware's local upgrade and by U-Boot web recovery); UBI on NAND; `sysupgrade` between OpenWrt builds keeps settings |
| Wi-Fi 2.4 GHz (IPQ5332 integrated radio, ath12k AHB) | Works. Calibration and MAC addresses from ART, board data for every GL variant |
| Wi-Fi 5 GHz (QCN6432, ath12k) | Works: AP, station, AP + station on one radio, multi-SSID, DFS, 160 MHz EHT (a Wi-Fi 6E client links at 2.4 Gbit/s and moves 1.4 to 1.6 Gbit/s TCP). The radio is a separate wiphy from the 2.4 GHz one. A firmware crash recovers both radios in about 2.5 s |
| Ethernet (PPE, 2× QCA8081) | Works, both ports at 2.5 Gbit/s. Against an external 2.5 GbE host the WAN port receives 2.35 Gbit/s over four TCP streams and about 2.0 Gbit/s over one, and transmits 2.33 Gbit/s, with zero receive FIFO overflows |
| USB 3.0 | Works: SuperSpeed and high-speed devices, UAS. The host controller is in both images; storage, filesystems and modem drivers are in the extended image |
| Fan | Works (PWM, tachometer) |
| Side screen (ST7789P3 76×284) | Driver built in: `/dev/fb0` is a landscape 284×76 RGB565 framebuffer, `/sys/class/backlight/backlight` (0–11). The stock image draws nothing on it; the extended image runs a spinner animation |
| Touch (CST816S) | Driver built in: `/dev/input/event0` reports `ABS_X`/`ABS_Y` in fb0 pixels, `BTN_TOUCH`, and the chip's swipe / long-press gestures as `KEY_UP`/`KEY_DOWN`/`KEY_LEFT`/`KEY_RIGHT`/`KEY_MENU` |
| Buttons | Reset button; the two-position slide switch is `BTN_0` with `TYPE=switch` |
| LEDs | None on this hardware |
| Cross-band MLO | Off by design: the two radios are separate wiphys, OpenWrt's model. Stock does MLO |

### Known limitations

- **RTL8811AU (11ac) USB clients cannot associate on 5 GHz.** The QCN6432
  never sees this adapter's ACKs. Other clients tested (Wi-Fi 6E, phones)
  are fine.
- **Board data covers GL's plain hardware variants only.** Suffixed variants
  (KCT, 33V, HP, LP board files) share a board id with a plain file and need
  a device-tree `variant` tag; untested.
- **No official package repository.** See "Adding packages" below.
- **No console on the panel** (`CONFIG_VT` is off).

## Hardware

| Block | Detail |
|---|---|
| SoC | Qualcomm IPQ5332 family (reports as IPQ5312), 4× Cortex-A53, board AP-MI04.1-C2 |
| RAM / flash | 1 GB DDR4 / Winbond W25N04KW 512 MB QSPI NAND (UBI), 128 KiB blocks, 2048 B pages |
| Wi-Fi 2.4 GHz | on-SoC 2×2 radio, ath12k over AHB |
| Wi-Fi 5 GHz | QCN6432 2×2, on an internal link owned by the SoC's Q6; Linux sees a "hybrid" AHB device (register window from QMI, GIC MSIs), no host PCIe |
| Ethernet | 2× QCA8081 2.5GBASE-T on PPE ports 1 (LAN) and 2 (WAN), SGMII in-band |
| USB | one USB 3.0 Type-A host port: dwc3 + M31 HS PHY + the PCIe0/USB combo uniphy as SuperSpeed PHY; VBUS switch on GPIO32 |
| Fan | PWM channel 2, supply enable GPIO29, tachometer GPIO31 |
| Screen | Sitronix ST7789P3 76×284 TFT on BLSP0 SPI, PWM backlight (channel 3) |
| Touch | Hynitron CST816S on bit-banged I²C (GPIO33/34) |
| Buttons | reset GPIO36, slide switch GPIO35 |
| Stock firmware | GL.iNet 4.9.2 (QSDK, kernel 5.4); wireless firmware WLAN.WBE.1.4 |

Flash layout (128 KiB erase blocks): `0:SBL1`, `0:MIBIB`, `0:BOOTCONFIG`,
`0:QSEE`, `0:DEVCFG`, `0:TME`, `0:CDT`, `0:APPSBLENV`, `0:APPSBL` (U-Boot),
`0:ETHPHYFW`, `0:TRAINING`, `0:ART` (calibration + MACs, 2 MiB at
0x0b80000), `0:LICENSE`, `CFG`, `log`, `rootfs` (UBI, 492 MiB at 0x1200000).
OpenWrt writes only inside `rootfs`.

## Images

| | Stock | Extended |
|---|---|---|
| Build seed | `configs/be3600.config` | `configs/be3600-extra.config` |
| File names | `openwrt-qualcommbe-ipq53xx-glinet_gl-be3600-…` | `openwrt-extra-qualcommbe-ipq53xx-glinet_gl-be3600-…` |
| Contents | OpenWrt's default packages for the target, the device's hardware packages (Wi-Fi driver, firmware and board data, fan) and LuCI: what an OpenWrt release image contains | Stock, plus the packages below and their dependencies |

Extended image, on top of stock:

- **USB modems:** QMI (`kmod-usb-net-qmi-wwan`, `uqmi`, `luci-proto-qmi`),
  MBIM (`kmod-usb-net-cdc-mbim`, `umbim`, `luci-proto-mbim`), NCM
  (`kmod-usb-net-cdc-ncm`, `kmod-usb-net-huawei-cdc-ncm`, `comgt-ncm`,
  `luci-proto-ncm`), PPP dial-up (`comgt`, `luci-proto-3g`), ECM and RNDIS
  (`kmod-usb-net-cdc-ether`, `kmod-usb-net-rndis`), Sierra DirectIP
  (`kmod-usb-net-sierrawireless`); the modems' serial AT ports
  (`kmod-usb-serial-option`, `kmod-usb-serial-sierrawireless`,
  `kmod-usb-serial-qualcomm`, with `kmod-usb-serial-wwan`), `kmod-usb-acm`,
  `kmod-usb-wdm`, `usb-modeswitch`, `kmod-usb2`, and `picocom` as an AT
  terminal.
- **Tethering and USB Ethernet:** iPhone (`kmod-usb-net-ipheth`, `usbmuxd`;
  Android uses RNDIS/ECM/NCM above), Realtek RTL8152/RTL8153
  (`kmod-usb-net-rtl8152`) and ASIX AX88179 (`kmod-usb-net-asix-ax88179`)
  adapters.
- **USB storage and file sharing:** `kmod-usb3`, `kmod-usb-storage`,
  `kmod-usb-storage-uas`, vfat, exfat, ntfs3, ext4 and f2fs, the NLS tables,
  `block-mount`, `blockd`, `usbutils`, and Samba (`luci-app-samba4`, which
  brings `samba4-server`).
- **VPN:** WireGuard (`luci-proto-wireguard`, which brings `kmod-wireguard`
  and `wireguard-tools`) and OpenVPN (`openvpn-openssl`,
  `luci-proto-openvpn`; OpenVPN is set up as an interface under Network →
  Interfaces, since LuCI no longer has a separate OpenVPN app).
- **Tools:** `openssl-util`, `tcpdump`, `nano`, `ethtool`; LuCI over HTTPS
  (`luci-ssl`).
- **Side-screen spinner** (`slate-spinner`), installed and started at boot.

ModemManager is not included: it takes over modems that the QMI/MBIM
protocols above expect to manage. Add `luci-proto-modemmanager` instead of
them if you prefer it.

## Installing

Images are produced under `bin/targets/qualcommbe/ipq53xx/`, for each image:

- `…-squashfs-factory.bin`: for the stock firmware and for U-Boot recovery.
- `…-squashfs-sysupgrade.bin`: for `sysupgrade` from an existing OpenWrt build.
- `…-initramfs-uImage.itb`: a kernel with the root filesystem in RAM, which
  OpenWrt builds by default for this target. It is not needed for installing
  and has not been tested on the Slate.

**From stock firmware.** Upload `factory.bin` on the stock local-upgrade page
with "keep settings" off. The stock firmware accepts it because the image lists
the board's reference-design compatible (`qcom,ipq5332-ap-mi04.1-c2`).

**U-Boot web recovery** (also the way back to stock). Power off, hold reset,
apply power and keep holding until the recovery mode starts. Set the PC to
192.168.1.2/24, browse to http://192.168.1.1, upload `factory.bin` (or the
stock `.bin`), wait about three minutes. U-Boot's address is always
192.168.1.1, whatever is installed.

**From OpenWrt.** `sysupgrade …-squashfs-sysupgrade.bin` keeps settings;
`sysupgrade -n` wipes them. Either image can replace the other this way.
`sysupgrade` refuses the factory image on purpose: written through the
sysupgrade path it would land in the kernel UBI volume and leave the device
unbootable. The upgrade also removes the stock firmware's `wifi_fw` UBI
volume, which OpenWrt does not use.

**First boot.** The LAN jack hands out DHCP leases from 192.168.1.1 (also the
failsafe address); the WAN jack is the uplink and takes its own address by
DHCP. LuCI and `ssh root@192.168.1.1`, no password. Both Wi-Fi radios are
present but disabled, as OpenWrt does by default.

**Regulatory country.** Both radios take the country code stored in the
unit's ART partition (offset 0x88), the same value the stock firmware reads:
through `board.json` on a fresh install and through a `uci-defaults` script on
an upgrade. Leaving the country unset does not give a world setting; the
radios then fall back to the firmware's own default. If the unit is used in a
different country, set **both** radios to it (LuCI: Network → Wireless →
radio → Advanced). Mismatched countries on the two radios evict a station
after about 60 s.

**USB note.** A device that fell back to USB 2 stays there until re-plugged
or the port is power-cycled, which is:

```
echo 8a00000.usb > /sys/bus/platform/drivers/dwc3-qcom/unbind
echo 8a00000.usb > /sys/bus/platform/drivers/dwc3-qcom/bind
```

**Repeater on one radio.** An AP and a station on the same radio share airtime
and the AP follows the station's channel (the AP stays disabled until the
station associates). Put the upstream link on the other radio where possible.

## Side screen

Both images expose the hardware:

- `/dev/fb0`: 284×76, 16 bpp RGB565, landscape, the right way up.
  `cat /dev/urandom > /dev/fb0` fills it with noise, `cat /dev/zero > /dev/fb0`
  clears it. Updates are pushed to the panel by fbtft's deferred I/O, at up
  to 40 fps, synchronised to the panel's tearing-effect line.
- `/sys/class/backlight/backlight/brightness`: 0–11. Off until the first
  frame reaches the panel; `FBIOBLANK` on fb0 switches it too.
- `/dev/input/event0`: touch, in fb0 coordinates (0–283, 0–75), plus the
  gesture keys listed above.

The stock image draws nothing on the screen. The extended image runs
`slate-spinner`: a cycling orange star beside a networking-themed phrase that
changes every 5 s, and a trail of random characters under a finger on the
screen. It is configured in `/etc/config/slate-spinner` (`enabled`,
`rotation`, `brightness` 0–11, `touch`). To turn it off:

```
uci set slate-spinner.main.enabled=0 && uci commit slate-spinner
/etc/init.d/slate-spinner stop
```

## Building

Build host: Linux. Verified on Ubuntu 24.04 with `build-essential clang flex
bison g++ gawk gettext git libncurses-dev libssl-dev python3
python3-setuptools rsync swig unzip zlib1g-dev file wget`.

```
git clone -b gl-be3600 https://github.com/fivefold3/openwrt-gl-be3600.git
cd openwrt-gl-be3600
./scripts/feeds update -a && ./scripts/feeds install -a

# stock image
cp configs/be3600.config .config
make defconfig
make -j$(nproc)

# extended image (reuses the toolchain and packages built above)
cp configs/be3600-extra.config .config
make defconfig
make -j$(nproc) EXTRA_IMAGE_NAME=extra
```

`EXTRA_IMAGE_NAME` only prefixes the file names, so both images end up side
by side in `bin/targets/qualcommbe/ipq53xx/`. `configs/be3600-debug.config`
is the stock image plus `ath12k` debug messages (`ath12k.debug_mask=`),
`ip-full` and `iperf3`; do not ship images built from it.

`feeds.conf.default` pins every package feed to the revision the tested
images were built from, so `./scripts/feeds update -a` fetches exactly those.
Dropping the `^<commit>` suffixes follows the feeds' current heads instead,
which this port has not been tested against.

The wireless firmware for both radios ships as the rootfs overlay under
`files/lib/firmware/ath12k/` (tracked in git; the tree's `.gitignore`
re-includes `/files`). The build signs packages and the firmware with a key
it generates on first use (`key-build`, `key-build.pub`).

## Adding packages

There is no OpenWrt.org package repository for this target. The feed list a
built image carries points at the OpenWrt snapshot feeds; the target feed,
which holds the kernel modules, does not exist there, and the other feeds
move daily, so they do not match this tree. Kernel modules only load on the
kernel they were built with. Build packages from this tree instead.

**Into the image** (they survive `sysupgrade`): add `CONFIG_PACKAGE_<name>=y`
lines to a seed, then build as above. For example, on top of the stock image:

```
cp configs/be3600.config .config
echo 'CONFIG_PACKAGE_luci-proto-wireguard=y' >> .config
echo 'CONFIG_PACKAGE_tcpdump=y' >> .config
make defconfig
make -j$(nproc) EXTRA_IMAGE_NAME=custom
```

`make menuconfig` does the same interactively (`/` searches). Find package
names with `./scripts/feeds search <word>`; `configs/be3600-extra.config`
is a longer example. `make defconfig` adds dependencies and silently drops a
name that does not exist, so check the result with
`grep CONFIG_PACKAGE_<name> .config`.

**Onto a running router:** set the package to `m` instead of `y`
(`CONFIG_PACKAGE_<name>=m`), run `make defconfig` and
`make package/<name>/compile`, then copy the `.apk` and any dependencies the
router lacks, and install them:

```
find bin -name '<name>-*.apk'
# userland:        bin/packages/aarch64_cortex-a53/<feed>/
# kernel modules:  bin/targets/qualcommbe/ipq53xx/packages/
scp -O <path>/<name>-*.apk root@192.168.1.1:/tmp/
ssh root@192.168.1.1 apk add --allow-untrusted /tmp/<name>-*.apk
```

Packages installed this way are not part of the image, so a `sysupgrade`
removes them; build them into the image to keep them. Kernel modules must
come from the same build as the image that is running.

## What this branch adds

Everything is in the commits on top of `b12c854`:

| Path | Purpose |
|---|---|
| `target/linux/qualcommbe/dts/ipq5332-gl-be3600.dts` | Device tree, derived from the stock firmware's DTB |
| `target/linux/qualcommbe/image/ipq53xx.mk`, `image/gl-be3600-factory.bootscript` | Device definition: LZMA kernel FIT (`config@mi04.1-c2`), UBI, GL-format factory image, hardware package list |
| `target/linux/qualcommbe/ipq53xx/config-default` | pstore/ramoops; framebuffer, fbtft, backlight, i2c-gpio, evdev and touch built in; ARMv8 CE AES/GHASH (from openwrt-flint3 `20ccc9b4c6`) |
| `target/linux/qualcommbe/ipq53xx/base-files/…` | network/MAC/country setup (`02_network`), OEM name, ath12k caldata extraction for both radios, sysupgrade handling and the factory-image guard (`platform.sh`), the country `uci-defaults` script, and a hotplug script that stops a firmware crash waiting five minutes for a coredump reader |
| `target/linux/qualcommbe/patches-6.18/2990`, `2991` | dwc3-qcom: interconnect paths up before the core probe; `mock_utmi` handed to the core as its 60 MHz reference clock. Without them a rebind hangs and high-speed devices fail every descriptor read |
| `target/linux/qualcommbe/patches-6.18/2995` | fbtft driver for the ST7789P3 panel (stock init sequence, frame-memory offsets 82/18, TE sync, backlight coupling) |
| `target/linux/qualcommbe/patches-6.18/2996` | hynitron-cst816x: gesture keys as press + release, generic touchscreen properties |
| `package/kernel/mac80211/patches/ath12k/320` | QCN6432 "hybrid bus" support (user PD 2 on the Q6, register window over QMI, static windows, platform MSIs, crash recovery). Generated by `scripts/gl-be3600/gen-qcn6432-patch.py`; regenerate it against pristine source after every mac80211 bump |
| `…/ath12k/327` | radio MAC addresses from the device tree (ART nvmem cell) |
| `…/ath12k/329` | do not drop a whole fw-stats event because an AP vdev has no peer (fixes LuCI stalling with AP + station on one radio) |
| `…/ath12k/330` | do not touch freed TX bank profiles after a failed recovery |
| `…/ath12k/331` | spread the AHB data-path interrupts over the CPUs |
| `target/linux/qualcommbe/patches-6.18/0425`, `0426` | EDMA Tx completion: bounds-check the fragment index and unmap the fragment's real address. Backported from openwrt-flint3 (`36652ea55b`, `4c566cece3`) |
| `package/network/config/wifi-scripts/…` | `disassoc_low_ack` defaults to 0, so stations are not kicked on spurious low-ACK reports. Backported from openwrt-flint3 (`514fc35ed1`, `88d31e06f9`) |
| `package/network/utils/iwinfo/patches/200`, `201` | device names for IPQ5332/QCN6432; scanning no longer leaves an interface up |
| `package/firmware/ipq-wifi/…/board-glinet_gl-be3600.ipq5332` | 2.4 GHz board data container |
| `package/utils/slate-spinner/` | the side-screen spinner (extended image) |
| `files/lib/firmware/ath12k/{IPQ5332,QCN6432}/hw1.0/` | wireless firmware and the 5 GHz board data container |
| `configs/` | build seeds: stock, extended, debug |
| `feeds.conf.default` | package feeds pinned to the tested revisions |
| `.gitignore` | re-includes `/files` so the firmware overlay is tracked |
| `scripts/gl-be3600/` | NAND backup script; generators for patch 320, the board data containers and the spinner's font |

## Firmware and board data

`files/lib/firmware/ath12k/IPQ5332/hw1.0/` and `…/QCN6432/hw1.0/` are the
WLAN.WBE.1.4 firmware files (`q6_fw*`, `iu_fw`, `Data.msc`, `regdb.bin`) as
shipped in the GL.iNet 4.9.2 firmware's `wifi_fw` volume. The QCN6432 needs
its own `iu_fw` from `qcn6432/` there. Both `board-2.bin` containers are
built by `scripts/gl-be3600/gen-qcn6432-board2.py` from GL's stock `bdwlan.b*`
files: every plain variant plus `bdwlan.bin` as the `qmi-board-id=255`
default, so any unit's board id matches. Calibration is not in the image; it
is read from the unit's ART partition at boot (offsets 0x1000 and 0x12800).

These files are Qualcomm / GL.iNet proprietary binaries redistributed as
found in the stock firmware; no licence is claimed for them.

## License

OpenWrt is GPL-2.0 (see `COPYING` and `LICENSES/`); the device tree, patches,
scripts and the spinner added here are under the same terms as the files they
extend. The spinner's glyphs are rasterized from DejaVu Sans (Bitstream Vera
licence).
