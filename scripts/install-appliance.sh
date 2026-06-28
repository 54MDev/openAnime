#!/usr/bin/env bash
#
# install-appliance.sh -- Milestone 5: turn the Uno Q into a boot-to-UI appliance.
#
# Run this ON the Uno Q after the backend (M2-M4) is verified working:
#     sudo bash scripts/install-appliance.sh
#
# It is idempotent -- safe to re-run after a `git pull`. It installs the systemd
# service, the Openbox autostart, and configures LightDM autologin so that
# powering on the device lands straight on the openAnime UI with no keyboard.
#
# Assumes the login user is "user" and the repo lives at /home/user/openAnime
# (the convention used throughout build-instructions.md). Override with env vars:
#     APP_USER=pi REPO_DIR=/opt/openAnime sudo -E bash scripts/install-appliance.sh
set -euo pipefail

APP_USER="${APP_USER:-user}"
REPO_DIR="${REPO_DIR:-/home/${APP_USER}/openAnime}"
USER_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: run as root (sudo bash scripts/install-appliance.sh)" >&2
  exit 1
fi
if [[ ! -d "${REPO_DIR}" ]]; then
  echo "error: repo not found at ${REPO_DIR}; set REPO_DIR=..." >&2
  exit 1
fi

# Fail fast if the kiosk/display packages aren't installed -- otherwise we'd
# half-configure autologin into a session that can't launch (black screen).
echo "==> Checking required packages"
REQUIRED_PKGS=(chromium openbox lightdm unclutter xorg x11-xserver-utils mpv wmctrl)
MISSING=()
for pkg in "${REQUIRED_PKGS[@]}"; do
  dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -q "install ok installed" || MISSING+=("${pkg}")
done
if [[ "${#MISSING[@]}" -gt 0 ]]; then
  echo "error: missing required packages: ${MISSING[*]}" >&2
  echo "install them first:" >&2
  echo "    sudo apt update && sudo apt install -y ${MISSING[*]}" >&2
  exit 1
fi

echo "==> Installing systemd service (user=${APP_USER}, repo=${REPO_DIR})"
# Render the unit with the real user/path rather than the hardcoded defaults.
sed -e "s#User=user#User=${APP_USER}#" \
    -e "s#/home/user/openAnime#${REPO_DIR}#g" \
    "${REPO_DIR}/systemd/openanime.service" > /etc/systemd/system/openanime.service
systemctl daemon-reload
systemctl enable openanime
systemctl restart openanime

echo "==> Installing Openbox autostart"
install -d -o "${APP_USER}" -g "${APP_USER}" "${USER_HOME}/.config/openbox"
install -m 644 -o "${APP_USER}" -g "${APP_USER}" \
  "${REPO_DIR}/appliance/openbox-autostart" \
  "${USER_HOME}/.config/openbox/autostart"

echo "==> Configuring LightDM autologin"
install -d /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-openanime.conf <<EOF
[Seat:*]
autologin-user=${APP_USER}
autologin-session=openbox
EOF

echo
echo "Done. Reboot to test the cold-boot path:"
echo "    sudo reboot"
echo
echo "Backend status:  systemctl status openanime"
echo "Backend logs:    journalctl -u openanime -f"
