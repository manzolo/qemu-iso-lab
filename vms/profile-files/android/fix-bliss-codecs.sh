#!/system/bin/sh
# Bliss 16.9.7 FOSS 20241011: the x86 Codec2 process searches ARM libraries first.
# Only its sphal search paths change. /linkerconfig is regenerated at every boot.
set -eu
fail() { echo "vmctl: $*" >&2; exit 1; }
[ "$(getprop ro.build.fingerprint)" = 'Android-x86/bliss_x86_64/x86_64:13/TQ3A.230901.001.C1/151:userdebug/test-keys' ] || fail 'unsupported Android build'
[ "$(getprop ro.bliss.version)" = '16.9.7' ] || fail 'requires Bliss 16.9.7'
[ "$(id -u)" = 0 ] || fail 'requires root ADB (adb root)'
[ "$(getprop ro.hardware.gralloc)" = minigbm_arcvm ] || fail 'requires virtio-gpu and GRALLOC=minigbm_arcvm'
[ "$(getprop debug.ui.default_mapper)" = 4 ] || fail 'requires GRALLOC4_MINIGBM=1'
config=/linkerconfig/com.android.media.swcodec/ld.config.txt
[ -f "$config" ] || fail 'Codec2 linker configuration missing'
pattern='^namespace\.sphal\..*search\.paths.*\/arm'
if ! grep -q "$pattern" "$config"; then
    echo 'Bliss Codec2 linker already repaired for this boot.'
    exit 0
fi
[ -f "$config.vmctl-original" ] || cp -p "$config" "$config.vmctl-original"
sed "/$pattern/d" "$config" > "$config.vmctl-fixed"
[ -s "$config.vmctl-fixed" ] || fail 'empty generated linker configuration'
# Preserve the inode: services may already see it through their mount namespace.
cat "$config.vmctl-fixed" > "$config"
setprop ctl.restart media.swcodec
echo 'Bliss Codec2 linker repaired in guest tmpfs; reboot restores the original.'
