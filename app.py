import eventlet
eventlet.monkey_patch()


from flask import Flask, render_template, request, redirect, url_for, session, make_response, jsonify
from werkzeug.utils import secure_filename
import api
import datetime
from flask_caching import Cache
import requests
import os
import random
from asgiref.wsgi import WsgiToAsgi
import razorpay
from urllib.parse import quote
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid
import urllib3
from collections import Counter
from flask_dance.contrib.google import make_google_blueprint, google
import os

import time
import firebase_admin
from firebase_admin import credentials, messaging


# Disable SSL warnings (for testing only, not recommended for production)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Ensure you use a secure key for session
app.config['SESSION_PERMANENT'] = True  # Make session persistent
app.config['SESSION_TYPE'] = 'filesystem'  # Store session on server

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

razorpay_client = razorpay.Client(auth=("rzp_test_SWjvcpME4fGCmq", "ZuTowFTbz7voWmL6VmstfMgc"))


# Upload folder configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# Google OAuth Setup
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Only for testing (http)
google_bp = make_google_blueprint(
    client_id="593225854148-v754trhokdtfcob2n4q8dk0mb1jbtsmi.apps.googleusercontent.com",
    client_secret="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",
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile"
    ],
    redirect_url="/login/google/authorized"  # matches Google Console redirect URI
)
app.register_blueprint(google_bp, url_prefix="/login")



# Only initialize once
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)



executor = ThreadPoolExecutor()

@app.route('/')
def index():
    return render_template('index.html')  # splash with FCM


@app.route('/save_token', methods=['POST'])
def save_token():
    username = request.cookies.get('username')
    data = request.get_json()
    token = data.get('token')
    public_ip = data.get('public_ip')

    print("username ", username)
    print("🔐 Received Firebase Token:", token)
    print("🌐 Public IP Address:", public_ip)

    session['fcm_token'] = token  # optional
    session['public_ip'] = public_ip

    executor.submit(api.insert_fmctoken, token, public_ip, username or "none")

    return jsonify({"status": "success"})


@app.route('/firebase-messaging-sw.js')
def sw():
    return app.send_static_file('firebase-messaging-sw.js')


@app.route("/main", methods=['GET', 'POST'])
def main():
    username = request.cookies.get('username')

    if username:
        # Launch insert_log in background thread
        executor.submit(api.insert_log, username, "main", "none")

        # fetch_userid runs in main thread (as we need its result)
        future_user = executor.submit(api.fetch_userid, username)
        user_data = future_user.result()

        name = user_data[0] if user_data else 'User'
        return render_template('main.html', name=name)
    
    else:
        # Run insert_log in background for anonymous user
        executor.submit(api.insert_log, "none", "main", "none")
        return render_template('main.html', name=None)


@app.route('/login', methods=['GET', 'POST'])
def login():
    username = request.cookies.get('username')

    if username:
        session['username'] = username
        # Log in background
        executor.submit(api.insert_log, username, "login", "none")
        return redirect(url_for('main'))

    if request.method == 'POST':
        number = request.form.get('number')
        password = request.form.get('password')

        if not number or not password:
            error = "Please enter both number and password"
            return render_template('login.html', error=error)

        print(number, password)
        checklogin = api.login(number, password)
        print("checklogin ", checklogin)

        # Log in background
        executor.submit(api.insert_log, number, "login", "none")

        if checklogin:
            session.clear()
            session['username'] = number
            expire_date = datetime.datetime.now() + datetime.timedelta(days=30)

            resp = make_response(redirect(url_for('main')))
            resp.set_cookie('username', number, expires=expire_date)

            return resp
        else:
            error = "Invalid login credentials!"
            return render_template('login.html', error=error)

    return render_template('login.html')


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        number = request.form.get("number", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Log action in background
        executor.submit(api.insert_log, number, "signup", "none")

        if password != confirm_password:
            error = "Passwords do not match"
            return render_template('signup.html', error=error)

        checkuser = api.checkuser(email, number)

        if not checkuser:
            otp = random.randint(1000, 9999)
            session['otp'] = otp
            print("Otp ", otp)

            session['signup_info'] = {
                'name': name,
                'email': email,
                'phone': number,
                'password': password
            }

            # Send OTP email in background
            executor.submit(api.send_mail, email, otp)

            return redirect(url_for('otp_verification'))
        else:
            return render_template("signup.html", error="User already exists!")
    
    return render_template("signup.html")


@app.route('/otp_verification', methods=['GET', 'POST'])
def otp_verification():
    if request.method == 'POST':
        user_info = session.get('signup_info')
        email = session.get('signup_info', {}).get('email', None)
        entered_otp = request.form.get('otp')
        ip_address = request.form.get('ip_address')

        stored_otp = str(session.get('otp'))  # Ensure both are strings

        print("ip_address: ", ip_address)

        if entered_otp == stored_otp:

            def threaded_send_otp(name, email, number, password, ip_address):
                status = api.signup(name, email, number, password, ip_address)
                print("OTP Status (in thread):", status)

            thread = threading.Thread(
                target=threaded_send_otp,
                args=(user_info.get("name"), user_info.get("email"), user_info.get("phone"), user_info.get("password"), ip_address)
            )
            thread.start()

            session.clear()
            session['username'] = user_info.get("phone")  # or use email if preferred

            expire_date = datetime.datetime.now() + datetime.timedelta(days=30)
            resp = make_response(redirect(url_for('main')))
            resp.set_cookie('username', user_info.get("phone"), expires=expire_date)

            return resp

            # return redirect(url_for('dashboard'))  # Update as needed

        else:
            return render_template("otp_verification.html", error="Enter valid OTP")
        
    else:
        # ✅ Return this for GET requests
        email = session.get('signup_info', {}).get('email', None)
        return render_template("otp_verification.html", email=email)


def parse_amenities(amenity_string):
    icon_map = {
        "food & beverages": "food.png",
        "screening": "screening.png",
        "cakes": "cakes.png",
        "decoration": "decoration.png",
        "bouquet": "bouquet.png",
        "photoshoot": "photoshoot.png",
    }

    items = [a.strip() for a in amenity_string.split(",")]
    amenities = []

    for item in items:
        key = item.lower()
        for keyword, icon in icon_map.items():
            if keyword in key:
                amenities.append({"icon": icon, "name": item})
                break

    return amenities


def parse_images(image_string):
    return [img.strip() for img in image_string.split(",")]


@app.route("/search")
def search():
    username = request.cookies.get('username')

    if username:
        # Launch insert_log in background thread
        executor.submit(api.insert_log, username, "search", "none")

        # fetch_userid runs in main thread (as we need its result)
        future_user = executor.submit(api.fetch_userid, username)
        user_data = future_user.result()

        print(user_data)

        name = user_data[0] if user_data else 'User'

        # Fetch theaters in parallel
        future_theaters = executor.submit(api.fetch_theaters)
        theater_list = future_theaters.result()
        print("theater_list_headers: name, about, price, aminities, policies, address, state, images, theater_id")
        print("theater_list:", theater_list)

        # Dynamically build theaters list from DB
        theaters = []
        for t in theater_list:
            theater = {
                "name": t[0],
                "price": t[2],
                "amenities": parse_amenities(t[3]),
                "address": t[5],
                "images": "1.webp",
                "id": t[8],
                "distance_km": 3.2,  # Optional: static or dynamically calculated
            }
            theaters.append(theater)

        print("theaters:", theaters)
    
        return render_template('search.html', name=name, theaters=theaters)
    
    else:
        # Run insert_log in background for anonymous user
        executor.submit(api.insert_log, "none", "search", "none")

        # Fetch theaters in parallel
        future_theaters = executor.submit(api.fetch_theaters)
        theater_list = future_theaters.result()
        print("theater_list:", theater_list)

        # Dynamically build theaters list from DB
        theaters = []
        for t in theater_list:
            theater = {
                "name": t[0],
                "price": t[2],
                "amenities": parse_amenities(t[3]),
                "address": t[5],
                "images": "1.webp",
                "id": t[8],
                "distance_km": 3.2,  # Optional: static or dynamically calculated
            }
            theaters.append(theater)

        print("theaters:", theaters)
    
        return render_template('search.html', name=None, theaters=theaters)


@app.route('/theaterdetails/<int:theater_id>', methods=['GET', 'POST'])
def theaterdetails(theater_id):
    username = request.cookies.get('username')

    # Fetch theater details in background
    future_theater = executor.submit(api.fetch_theater_details, theater_id)
    theater_data = future_theater.result()

    print("theater_data ", theater_data)

    # Parse theater data into correct format
    theater = {
        "id": theater_data.get("theater_id", theater_id),
        "name": theater_data.get("name"),
        "address": theater_data.get("address"),
        "images": parse_images(theater_data.get("images", "")),
        "amenities": parse_amenities(theater_data.get("aminities", "")),
        "about": theater_data.get("about"),
        "policies": [p.strip() for p in theater_data.get("policies", "").split(",")],
        "price_per_hour": int(theater_data.get("price", 0)),
        "slots": [
            "10:00 AM - 12:00 PM", "12:00 PM - 02:00 PM",
            "02:00 PM - 04:00 PM", "04:00 PM - 06:00 PM",
            "06:00 PM - 08:00 PM", "08:00 PM - 10:00 PM",
            "10:00 PM - 12:00 AM"
        ],
        "booked_slots": theater_data.get("booked_slots")
    }

    print("theater ", theater)

    if username:
        executor.submit(api.insert_log, username, "theaterdetails", "none")
        future_user = executor.submit(api.fetch_userid, username)
        user_data = future_user.result()
        name = user_data[0] if user_data else 'User'

        return render_template("theaterdetails.html", name=name, theater=theater)
    else:
        executor.submit(api.insert_log, "none", "theaterdetails", "none")
        name = None
        return render_template("theaterdetails.html", name=None, theater=theater)
        
    

@app.route('/profile')
def profile():
    username = request.cookies.get('username')
    print("username ", username)

    if not username:
        executor.submit(api.insert_log, "None", "profile", "none")
        return redirect(url_for('login'))
    
    else:
        future_user = executor.submit(api.fetch_userid, username)
        user_data = future_user.result()
        name = user_data[0] if user_data else 'User'
        executor.submit(api.insert_log, name, "profile", "none")

        fetch_booking_history = api.fetch_user_booking(username)

        print("fetch_booking_history: ", fetch_booking_history)

        # Convert raw booking data to renderable format
        bookings = []
        for booking in fetch_booking_history:
            bookings.append({
                "theater_name": booking["theater_name"],  # Or fetch actual name from another table if needed
                "image_url": f"images/theater_images/{booking['theater_id']}/1.webp",
                "date": datetime.datetime.strptime(booking["booking_date"], "%Y-%m-%d").strftime("%d %B %Y"),
                "time": booking["time_slot"],
                "people": booking["no_people"],
                "status": booking["status"],
                "amount_paid": booking["price"]
            })

        return render_template('profile.html', name=name, bookings=bookings)


@app.route('/place_book', methods=['GET', 'POST'])
def place_book():
    username = request.cookies.get('username')

    if not username:
        return redirect(url_for('login'))

    if request.method == 'POST':
        theater_id = request.form.get('theater_id')
        selected_slot = request.form.get('selected_slot')
        selected_date = request.form.get('selected_date')
        name = request.form.get('name')
        address = request.form.get('address')

        print("theater_id:", theater_id)
        print("selected_slot:", selected_slot)
        print("selected_date:", selected_date)
        print("name:", name)
        print("address:", address)

        return redirect(url_for('booking', 
            theater_id=theater_id, 
            selected_slot=selected_slot, 
            selected_date=selected_date, 
            name=name, 
            address=address
        ))


@app.route('/booking', methods=['GET', 'POST'])
def booking():
    username = request.cookies.get('username')

    future_user = executor.submit(api.fetch_userid, username)
    user_data = future_user.result()
    username_name = user_data[0] if user_data else 'User'

    if not username:
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name')
        people_count = request.form.get('people_count')
        whatsapp = request.form.get('whatsapp')
        email = request.form.get('email')
        decoration = request.form.get('decoration')
        total_price = request.form.get('total_price')
        theater_id = request.form.get('theater_id')
        selected_date = request.form.get('selected_date')
        selected_time = request.form.get('selected_time')

        booking_id = f"BK{int(time.time() * 1000)}{random.randint(100, 999)}"

        print(f"Received Booking: Name={name}, selected_date={selected_date}, selected_time={selected_time}, "
              f"People={people_count}, WhatsApp={whatsapp}, Email={email}, Decoration={decoration}, "
              f"Total Price={total_price}, theater_id={theater_id}")

        # We'll store the result of the thread here
        result = {}

        def process_booking():
            success = api.theater_booking(
                theater_id, username, total_price, people_count,
                selected_date, selected_time, decoration,
                name, whatsapp, email, booking_id, "pending"
            )
            result['success'] = success

        # Start and wait for thread to complete
        thread = threading.Thread(target=process_booking)
        thread.start()
        thread.join()

        # ✅ Store session only after thread completes
        if result.get('success') is True:
            session['theater_id'] = theater_id
            session['booking_id'] = booking_id
            session['name'] = name
            session['phone'] = whatsapp
            session['email'] = email
            session['price'] = total_price
            session['username'] = username

            print(f"[✅] Booking confirmed: {booking_id}")
            return redirect("/checkout")

        else:
            print(f"[❌] Booking failed: {booking_id}")
            return redirect("/paymentfailed")
        
        
    else: 
        name = request.args.get('name')
        address = request.args.get('address')
        selected_date = request.args.get('selected_date')
        selected_time = request.args.get('selected_slot')
        theater_id = request.args.get('theater_id')  # ✅ Correct

        return render_template(
            "booking.html",
            username_name=username_name,
            name=name,
            address=address,
            selected_date=selected_date,
            selected_time=selected_time,
            theater_id=theater_id  # ✅ pass it to the template
        )


@app.route("/checkout", methods=["GET"])
def checkout():
    username = request.cookies.get('username')

    if not username:
        return redirect(url_for('login'))
    
    return render_template("checkout.html", 
        name=session.get('name'),
        phone=session.get('phone'),
        address=session.get('email'),  # "email" as address to match your template
        price=session.get('price'),
        booking_id=session.get('booking_id'),
        theater_id=session.get('theater_id'),
        username=session.get('username')
    )


@app.route("/create_order", methods=["POST"])
def create_order():
    data = request.get_json()
    amount = int(float(data["price"]) * 100)  # Convert ₹ to paise

    order = razorpay_client.order.create({
        "amount": amount,
        "currency": "INR",
        "payment_capture": 1
    })
    return jsonify(order)


@app.route("/verify", methods=["POST"])
def verify():
    username = request.cookies.get('username')
    data = request.get_json()

    print("data ", data)

    try:
        params_dict = {
            'razorpay_order_id': data['order_id'],
            'razorpay_payment_id': data['payment_id'],
            'razorpay_signature': data['signature']
        }


        razorpay_client.utility.verify_payment_signature(params_dict)

        print("=== Payment Verified Successfully ===")
        print("Payment ID:", data['payment_id'])
        print("Order ID:", data['order_id'])
        print("User ID:", data['order_id'])
        print("Signature:", data['signature'])
        print("price:", str(data['user']['price']))
        print("theater_id:", str(data['user']['theater_id']))
        print("username:", str(data['user']['username']))

        # Prepare payment details for insert_payment
        payment_details = {
            "cf_payment_id": data['payment_id'],
            "order_id": data['order_id'],
            "order_amount": str(int(data['user']['price'])),  # Convert paise to INR
            "payment_currency": "INR",
            "payment_amount": str(int(data['user']['price'])),
            "payment_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payment_completion_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payment_status": "SUCCESS",
            "payment_message": "Payment verified successfully",
            "bank_reference": data.get('bank_reference', ""),
            "payment_group": data.get('payment_method', "card"),  # Default to card if not provided
            "payment_method": data.get('payment_method', "card"),
            "uniqueid": str(data['user']['theater_id']),
            "contact": data.get('user', {}).get('phone', ""),
            "username": str(data['user']['username'])
        }

        print("payment_details ", payment_details)

        # Call insert_payment
        if api.insert_payment(payment_details):
            print("Payment details saved to database.")
        else:
            print("Failed to save payment details to database.")


        if 'user' in data:
            print("=== User Details ===")
            print("Name:", data['user']['name'])
            print("Phone:", data['user']['phone'])
            print("Email:", data['user']['email'])
            print("Price:", data['user']['price'])
            print("Order_id:", data['user']['order_id'])
            print("Theater_id:", data['user']['theater_id'])
            print("Username:", data['user']['username'])


        #api.insert_orders(str(data['user']['theater_id']), str(data['user']['username']), str(data['user']['price']), "pending", str(data['order_id']))
        # confirm_booking = api.update_theater_booking("confirm", str(data['order_id']))

        # print("confirm_booking ", confirm_booking)
        
        return jsonify({ "redirect_url": "/paymentsuccess" })

    except Exception as e:
        # booking_id=session.get('booking_id')

        # confirm_booking = api.update_theater_booking("failed", booking_id)

        print("Payment verification failed:", e)
        return jsonify({ "redirect_url": "/paymentfailed" })


@app.route("/paymentsuccess")
def paymentsuccess():
    name=session.get('name')
    phone=session.get('phone')
    address=session.get('email')
    price=session.get('price')
    booking_id=session.get('booking_id')
    theater_id=session.get('theater_id')
    username=session.get('username')

    print("name ", name)
    print("phone ", phone)
    print("address ", address)
    print("price ", price)
    print("booking_id ", booking_id)
    print("theater_id ", theater_id)
    print("username ", username)

    def confirm_booking_thread():
        success = api.update_theater_booking("confirm", booking_id)
        print("[🎯] confirm_booking success:", success)

    # Start the background thread
    thread = threading.Thread(target=confirm_booking_thread)
    thread.start()

    return render_template("paymentsuccess.html")


@app.route("/paymentfailed")
def paymentfailed():
    name=session.get('name')
    phone=session.get('phone')
    address=session.get('email')
    price=session.get('price')
    booking_id=session.get('booking_id')
    theater_id=session.get('theater_id')
    username=session.get('username')

    print("name ", name)
    print("phone ", phone)
    print("address ", address)
    print("price ", price)
    print("booking_id ", booking_id)
    print("theater_id ", theater_id)
    print("username ", username)

    def mark_booking_failed():
        success = api.update_theater_booking("failed", booking_id)
        print("[❌] Booking marked as failed:", success)

    # Start background thread
    thread = threading.Thread(target=mark_booking_failed)
    thread.start()

    return render_template("paymentfailed.html")


@app.route("/contactus")
def contactus():
    return render_template('contactus.html')


@app.route("/login/google/authorized")
def google_login_authorized():
    print("[Authorized Route] google.authorized:", google.authorized)

    if not google.authorized:
        print("[Redirecting] Not authorized.")
        return redirect(url_for("google.login"))

    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        print("[Error] Failed to fetch user info", resp.text)
        return "Failed to fetch user info", 400

    info = resp.json()
    print("[Google Info]", info)
    session["user"] = info["email"]
    return redirect("/dashboard")


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        name = request.form.get('name')
        address = request.form.get('address')
        price = request.form.get('price')
        about = request.form.get('about')
        policies = request.form.get('policies')
        amenities = request.form.getlist('amenities')
        images = request.files.getlist('images')

        saved_images = []

        for image in images:
            if image and allowed_file(image.filename):
                original_filename = secure_filename(image.filename)
                ext = os.path.splitext(original_filename)[1]  # keep .jpg/.jpeg
                unique_filename = f"{uuid.uuid4().hex}{ext}"  # e.g., abcd123.jpg
                path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                image.save(path)
                saved_images.append(unique_filename)
            else:
                print('Only JPG images are allowed.', 'danger')
                return redirect(request.url)

        # Simulate saving to DB
        print("🎬 Theater Saved:")
        print("Name:", name)
        print("Address:", address)
        print("Price:", price)
        print("Amenities:", amenities)
        print("About:", about)
        print("Policies:", policies)
        print("Images:", saved_images)

        return redirect(url_for('upload'))

    return render_template('upload.html')

if __name__ == '__main__':
    # app.run(debug=True)
    # app = WsgiToAsgi(app)
    #app.run(port=8080, debug=True)
    socketio.run(app, debug=True, port=4040, allow_unsafe_werkzeug=True)
