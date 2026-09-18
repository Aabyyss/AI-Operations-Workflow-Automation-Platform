# Account Access & Password Reset Runbook

Acme Software Account Access Procedures (v1.8)

## Self-service password reset

Customers can reset passwords from the login page using the "Forgot password"
link. The reset email arrives within 2 minutes and the link is valid for 60
minutes. This resolves the majority of access issues and requires no support
involvement.

## Locked accounts

After 5 failed login attempts, accounts lock for 15 minutes automatically.
Support agents should not manually unlock accounts; the automatic timer is a
security control. If a customer is still locked after 15 minutes, verify
identity via the registered email address, then trigger a reset from the admin
console.

## Two-factor authentication issues

Customers who lost their 2FA device must submit a signed identity
verification form. Support must never disable 2FA based on email requests
alone. Escalate all 2FA removals to the security team.

## Security rules

Never send passwords by email or chat. Never share account data with anyone
who cannot verify control of the registered email address.
