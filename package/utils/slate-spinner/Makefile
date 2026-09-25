#
# slate-spinner: a "working" spinner animation and touch demo for the
# GL.iNet Slate 7 (GL-BE3600) side screen. Part of the extended image
# (configs/be3600-extra.config); installable as a package on the stock one.
# The panel, backlight and touch drivers it uses are built into the kernel.
#

include $(TOPDIR)/rules.mk

PKG_NAME:=slate-spinner
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/slate-spinner
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Spinner animation for the GL.iNet Slate 7 side screen
  DEPENDS:=@TARGET_qualcommbe_ipq53xx
endef

define Package/slate-spinner/description
  Draws a "working" spinner on the Slate 7's 284x76 side screen: a cycling
  orange star and a white networking-themed phrase (a new one every 5 s)
  revealed with a block-cursor transition. While a finger is on the screen,
  random characters spew out from under it and fade away. Runs as a procd
  service (/etc/init.d/slate-spinner, START=99) that waits for the
  in-kernel panel driver's /dev/fb0; configured by /etc/config/slate-spinner
  (enabled, rotation, brightness, touch trail).
endef

define Package/slate-spinner/conffiles
/etc/config/slate-spinner
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/slate-spinner \
		$(PKG_BUILD_DIR)/slate-spinner.c $(TARGET_LDFLAGS) -lm
endef

define Package/slate-spinner/install
	$(INSTALL_DIR) $(1)/usr/bin $(1)/etc/init.d $(1)/etc/config
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/slate-spinner $(1)/usr/bin/
	$(INSTALL_BIN) ./files/slate-spinner.init $(1)/etc/init.d/slate-spinner
	$(INSTALL_CONF) ./files/slate-spinner.config $(1)/etc/config/slate-spinner
endef

$(eval $(call BuildPackage,slate-spinner))
