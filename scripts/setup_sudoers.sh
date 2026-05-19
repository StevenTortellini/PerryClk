#!/bin/bash
# Run once on the Pi to allow the clk-app service user to apply
# network and time settings without a sudo password prompt.
#
# Usage (on the Pi):
#   sudo bash scripts/setup_sudoers.sh
#
# The service runs as the user in clk-app.service (default: pi).
# Change SERVICE_USER below if you run the app as a different user.

SERVICE_USER="${1:-pi}"
SUDOERS_FILE="/etc/sudoers.d/clk-app"

cat > "$SUDOERS_FILE" <<EOF
# clk-app: allow time and network config without password
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-timezone *
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp *
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-time *
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/dhcpcd.conf
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/systemd/timesyncd.conf
$SERVICE_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart systemd-timesyncd
EOF

chmod 0440 "$SUDOERS_FILE"

# Validate — visudo will catch syntax errors before they lock you out
visudo -c -f "$SUDOERS_FILE" && echo "Sudoers entry written OK for user: $SERVICE_USER" \
    || (rm "$SUDOERS_FILE" && echo "ERROR: sudoers syntax check failed — file removed")
