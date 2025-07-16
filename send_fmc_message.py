import firebase_admin
from firebase_admin import credentials, messaging

# Initialize Firebase app (only once)
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

def send_fcm_notification(token, title, body):
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        token=token
    )
    try:
        response = messaging.send(message)
        print("✅ Successfully sent message:", response)
        return True
    except Exception as e:
        print("❌ Failed to send message:", e)
        return False

