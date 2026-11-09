REQUIRE_IMAGE_METADATA=1
RAMFS_COPY_BIN='dumpimage fitblk fit_check_sign'

gl_be6500_remove_oem_rootfs() {
	local mtdnum
	local ubidev
	local ubivol

	mtdnum=$(find_mtd_index "$CI_UBIPART")
	if [ -z "$mtdnum" ]; then
		echo "Unable to find UBI MTD partition $CI_UBIPART"
		return 1
	fi

	ubidev=$(nand_find_ubi "$CI_UBIPART")
	if [ -z "$ubidev" ]; then
		ubiattach --mtdn="$mtdnum" || return 1
		ubidev=$(nand_find_ubi "$CI_UBIPART")
	fi

	[ -n "$ubidev" ] || return 1

	ubivol=$(nand_find_volume "$ubidev" ubi_rootfs)
	[ -z "$ubivol" ] || {
		echo "Removing legacy ubi_rootfs volume"
		ubirmvol "/dev/$ubidev" --name=ubi_rootfs || return 1
	}
}

# Stock GL NAND firmware keeps QSDK wifi firmware in its own UBI volume;
# OpenWrt ships firmware in the rootfs, so reclaim the space.
gl_remove_oem_wifi_fw() {
	local ubidev
	local ubivol

	ubidev=$(nand_find_ubi "$CI_UBIPART")
	[ -n "$ubidev" ] || return 0

	ubivol=$(nand_find_volume "$ubidev" wifi_fw)
	[ -z "$ubivol" ] || {
		echo "Removing legacy wifi_fw volume"
		ubirmvol "/dev/$ubidev" --name=wifi_fw || return 1
	}
}

# Stock firmware images and our factory.bin are QSDK container FITs whose
# "ubi" section holds the whole ubinized UBI image of the rootfs partition.
# Extract it (same section resolution as stock's platform_prepare_sysupgrade)
# and write it with nand_upgrade_ubinized (detach + ubiformat the rootfs MTD),
# the same route stock's do_flash_ubi takes on this 2K-page NAND. This makes
# returning to stock firmware from OpenWrt a plain sysupgrade.
gl_be3600_do_upgrade_fit() {
	local fullname pos

	fullname=$(dumpimage -l "$1" | sed -n 's,^ Image [0-9]* (\([^)]*\)),\1,p' | grep "^ubi" | head -n1)
	[ -n "$fullname" ] || {
		echo "Unable to find ubi section in $1"
		return 1
	}

	pos=$(dumpimage -l "$1" | grep "(${fullname})" | awk '{print $2}')
	[ -n "$pos" ] || return 1

	dumpimage -o /tmp/ubi.bin -T flat_dt -p "$pos" "$1" > /dev/null || return 1

	nand_upgrade_ubinized /tmp/ubi.bin cat
}

platform_do_upgrade() {
	case "$(board_name)" in
	gl.inet,gl-be3600)
		CI_UBIPART="rootfs"
		if dumpimage -l "$1" >/dev/null 2>&1; then
			# Stock firmware / factory FIT: replace the whole UBI and
			# never carry OpenWrt config into the stock system.
			UPGRADE_BACKUP=""
			gl_be3600_do_upgrade_fit "$1"
		else
			gl_be6500_remove_oem_rootfs || return 1
			gl_remove_oem_wifi_fw || return 1
			nand_do_upgrade "$1"
		fi
		;;
	gl.inet,gl-be6500)
		CI_UBIPART="rootfs"
		gl_be6500_remove_oem_rootfs || return 1
		nand_do_upgrade "$1"
		;;
	gl.inet,gl-be9300)
		CI_KERNPART="0:HLOS"
		CI_ROOTPART="rootfs"
		glinet_emmc_do_upgrade "$1" || exit 1
		;;
	ubnt,u7-pro-xgs)
		CI_KERNPART="kernel0"
		fit_do_upgrade "$1"
		;;
	*)
		echo "Sysupgrade is not supported on your board yet."
		return 1
		;;
	esac
}

platform_check_image() {
	[ "$#" -gt 1 ] && return 1

	case "$(board_name)" in
	gl.inet,gl-be3600)
		# FIT images: the stock firmware and our factory.bin are QSDK
		# container FITs whose "ubi" section holds the whole ubinized UBI
		# of the rootfs partition. Accept them here and flash them via
		# gl_be3600_do_upgrade_fit - the same route stock's do_flash_ubi
		# takes (extract ubi, ubiformat rootfs). A FIT without an ubi
		# section (e.g. a bare kernel .itb) is rejected: nand_do_upgrade
		# would write the whole container into the kernel UBI volume and
		# leave the device unbootable.
		if dumpimage -l "$1" >/dev/null 2>&1; then
			dumpimage -l "$1" | grep -q "^ Image.*(ubi" || {
				echo "Invalid image: no ubi section found"
				return 1
			}
			return 0
		fi
		# The tar is built as sysupgrade-glinet_gl-be3600/ - the image profile
		# name, NOT board_name (gl.inet,gl-be3600). nand_do_platform_check only
		# substitutes commas and underscores, so it can never turn "gl.inet"
		# into "glinet"; passing board_name here rejects every valid image.
		# (Measured: CONTROL is 23 bytes under glinet_gl-be3600, 0 under the
		# board_name spelling. This is likely why the BE6500 arm is a bare
		# "return 0".)
		nand_do_platform_check "glinet_gl-be3600" "$1"
		;;
	gl.inet,gl-be6500)
		return 0
		;;
	gl.inet,gl-be9300)
		glinet_emmc_check_image "$1"
		;;
	ubnt,u7-pro-xgs)
		fit_check_image "$1"
		;;
	*)
		echo "Sysupgrade is not supported on your board yet."
		return 1
		;;
	esac
}

platform_copy_config() {
	case "$(board_name)" in
	gl.inet,gl-be9300|\
	ubnt,u7-pro-xgs)
		emmc_copy_config
		;;
	esac
}
