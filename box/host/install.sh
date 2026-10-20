#!/usr/bin/env bash
# Turns a fresh Ubuntu 24.04 mini PC into a Rasova box. Run once, as root, at
# Rasova's desk: it needs the internet this one time (for Docker), never again.
#
#   sudo bash install.sh <bundle folder> [--backup-disk /dev/sdb1] [--keep-ssh]
#
# Before running it:
#   * BIOS: "Restore on AC power loss" = Power On (the box comes back by itself
#     after a power cut), and a BIOS password.
#   * Ubuntu: install with "Encrypt the disk" (LUKS; TPM unlock if the machine
#     has one, else a passphrase typed at boot), so a stolen box doesn't hand
#     over the bills.
#   * A backup drive (a second disk or a USB stick), formatted ext4
#     (mkfs.ext4 -L rasova-backups /dev/sdX1): this script never formats.
#
# What it does: Docker, the name rasova.local, a firewall that only lets the
# app in, no sleep, small system logs, Indian time, no surprise system
# updates, the backup drive mounted at /srv/rasova/backups, then
# rasova-box install <bundle>.
set -euo pipefail

say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ $# -ge 1 ] || die "usage: install.sh <bundle folder> [--backup-disk DEVICE] [--keep-ssh]"
BUNDLE="$1"; shift
BACKUP_DISK=""; KEEP_SSH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --backup-disk) BACKUP_DISK="${2:?--backup-disk needs a device}"; shift 2 ;;
    --keep-ssh) KEEP_SSH=1; shift ;;
    *) die "unknown option $1" ;;
  esac
done
[ "$(id -u)" = 0 ] || die "run it with sudo"
[ -d "$BUNDLE" ] || die "no bundle folder at $BUNDLE"
HERE="$(cd "$(dirname "$0")" && pwd)"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "warning: this was made for Ubuntu 24.04; carrying on"

say "1/8 Docker and the tools"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io docker-compose-v2 avahi-daemon ufw openssl
systemctl enable --now docker

say "2/8 the name rasova.local"
hostnamectl set-hostname rasova
systemctl enable --now avahi-daemon

say "3/8 firewall: only the app comes in"
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 5353/udp                     # so phones can find rasova.local
if [ "$KEEP_SSH" = 1 ]; then
  ufw allow 22/tcp
else
  systemctl disable --now ssh.socket ssh.service 2> /dev/null || true
fi
ufw --force enable

say "4/8 never sleeps, small system logs, Indian time"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/rasova.conf
systemctl restart systemd-journald
timedatectl set-timezone Asia/Kolkata
timedatectl set-ntp true || true       # corrects the clock whenever the internet happens to be there
hwclock --systohc || true

say "5/8 no surprise system updates (they could restart things during service)"
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer 2> /dev/null || true

say "6/8 the backup drive"
mkdir -p /srv/rasova/backups
if [ -n "$BACKUP_DISK" ]; then
  UUID="$(blkid -s UUID -o value "$BACKUP_DISK" || true)"
  FSTYPE="$(blkid -s TYPE -o value "$BACKUP_DISK" || true)"
  [ -n "$UUID" ] || die "$BACKUP_DISK has no filesystem: format it first (mkfs.ext4 -L rasova-backups $BACKUP_DISK)"
  case "$FSTYPE" in
    ext4|xfs|btrfs) OPTS="defaults,nofail,x-systemd.device-timeout=10" ;;
    vfat|exfat) OPTS="defaults,nofail,x-systemd.device-timeout=10,uid=1000,gid=1000,umask=077" ;;
    *) die "$BACKUP_DISK is $FSTYPE; use ext4" ;;
  esac
  # nofail: if the stick is missing the box still starts, and backups go to
  # the main disk (the health page says the backup is on the same drive).
  grep -q "UUID=$UUID" /etc/fstab || echo "UUID=$UUID /srv/rasova/backups $FSTYPE $OPTS 0 2" >> /etc/fstab
  systemctl daemon-reload
  mount /srv/rasova/backups
else
  echo "no --backup-disk: backups stay on the main disk until one is added"
fi

say "7/8 rasova-box"
install -m 755 "$HERE/rasova-box" /usr/local/bin/rasova-box

say "8/8 Rasova"
rasova-box install "$BUNDLE"
