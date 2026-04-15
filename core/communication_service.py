import requests
import os
import logging

logger = logging.getLogger("pos.comms")

class CommunicationService:
    @staticmethod
    def send_whatsapp_bill(phone, order_url):
        """
        Sends the digital bill link to a customer via WhatsApp.
        Placeholder for Twilio or Razorpay WhatsApp API.
        """
        api_key = os.getenv("WHATSAPP_API_KEY")
        if not api_key:
            logger.warning("WhatsApp API key missing. Message simulated.")
            return True
            
        # Example using a generic WhatsApp API endpoint
        url = "https://api.comms-provider.com/v1/whatsapp/send"
        payload = {
            "to": phone,
            "message": f"Thank you for dining! View your bill هنا: {order_url}"
        }
        try:
            # response = requests.post(url, json=payload, headers={"Auth": api_key})
            # return response.status_code == 200
            return True
        except Exception as e:
            logger.error(f"WhatsApp send failed: {e}")
            return False

    @staticmethod
    def send_sms_loyalty(phone, points):
        """Placeholder for SMS loyalty notifications."""
        logger.info(f"Loyalty SMS: Your balance is {points} points.")
        return True
