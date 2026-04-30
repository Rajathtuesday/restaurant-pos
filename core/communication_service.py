import requests
import os
import logging

logger = logging.getLogger("pos.comms")

class CommunicationService:
    @staticmethod
    def send_whatsapp_bill(phone, bill_url, customer_name="Guest"):
        api_key = os.getenv("INTERAKT_API_KEY", "")
        if not api_key:
            return False, "WhatsApp not configured"

        response = requests.post(
            "https://api.interakt.ai/v1/public/message/",
            headers={"Authorization": f"Basic {api_key}",
                     "Content-Type": "application/json"},
            json={
                "countryCode": "+91",
                "phoneNumber": phone,
                "type": "Template",
                "template": {
                    "name": "bill_notification",
                    "languageCode": "en",
                    "bodyValues": [customer_name, bill_url]
                }
            }
        )
        return response.status_code == 200, response.text

    @staticmethod
    def send_sms_loyalty(phone, points):
        """Placeholder for SMS loyalty notifications."""
        logger.info(f"Loyalty SMS: Your balance is {points} points.")
        return True
