#!/usr/bin/env bash
# Create an RCS *testing* agent on AWS End User Messaging and submit its test registration.
# Brand assets: media/rcs/{logo,banner}.png. Writes IDs to build/rcs.env.
set -euo pipefail
cd "$(dirname "$0")/.."
A() { aws pinpoint-sms-voice-v2 "$@"; }
read -r AGENT_ID AGENT_ARN < <(A create-rcs-agent --query "[RcsAgentId,RcsAgentArn]" --output text)
echo "agent $AGENT_ID"
REG=$(A create-registration --registration-type TEST_RCS_LAUNCH_REGISTRATION --query RegistrationId --output text)
echo "registration $REG"
A create-registration-association --registration-id "$REG" --resource-id "$AGENT_ID" >/dev/null
LOGO=$(A create-registration-attachment --attachment-body fileb://media/rcs/logo.png --query RegistrationAttachmentId --output text)
BAN=$(A create-registration-attachment --attachment-body fileb://media/rcs/banner.png --query RegistrationAttachmentId --output text)
t() { A put-registration-field-value --registration-id "$REG" --field-path "$1" --text-value "$2" >/dev/null; }
c() { A put-registration-field-value --registration-id "$REG" --field-path "$1" --select-choices "$2" >/dev/null; }
f() { A put-registration-field-value --registration-id "$REG" --field-path "$1" --registration-attachment-id "$2" >/dev/null; }
t agentDetails.brandName "Settle Demo"; t agentDetails.senderDisplayName "Settle Demo"
t agentDetails.serviceName "Settle Demo RCS Agent"
t agentDetails.agentDescription "Demo billing assistant: explains medical bills and sets up payment plans."
t agentDetails.accentColor "#0F4C5C"
t agentDetails.contactPhoneNumber "+12065550100"; t agentDetails.contactPhoneLabel "Call Us"
t agentDetails.contactEmailAddress "hello@settle-demo.example.com"; t agentDetails.contactEmailLabel "Email Us"
t agentDetails.contactWebsite "https://www.settle-demo.example.com"; t agentDetails.contactWebsiteLabel "Visit Website"
t agentDetails.privacyPolicyUrl "https://www.example.com/privacy"; t agentDetails.privacyPolicyLabel "Privacy Policy"
t agentDetails.termsAndConditionsUrl "https://www.example.com/terms"; t agentDetails.termsAndConditionsLabel "Terms and Conditions"
t agentDetails.monthlyRcsVolume "1000"
t complianceKeywords.helpResponse "Settle Demo billing. Reply STOP to opt out. Help: hello@settle-demo.example.com"
t complianceKeywords.stopResponse "You have been unsubscribed. No more messages will be sent."
c agentDetails.useCase TRANSACTIONAL; c agentDetails.billingCategory CONVERSATIONAL
c agentDetails.averageMonthlyRcsFrequency 10
f agentDetails.logoImage "$LOGO"; f agentDetails.bannerImage "$BAN"
A submit-registration-version --registration-id "$REG" --query "[VersionNumber,RegistrationVersionStatus]" --output text
mkdir -p build && printf 'RCS_AGENT_ID=%s\nRCS_AGENT_ARN=%s\nRCS_REG_ID=%s\n' "$AGENT_ID" "$AGENT_ARN" "$REG" > build/rcs.env
cat build/rcs.env
