from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import os
import mysql.connector
from datetime import datetime
import qrcode
import requests
import base64
import time
from predict_food import predict_food
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "super_secret_key"

UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =============================
# DATABASE CONNECTION
# =============================
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="root",
    database="FoodCalorie_DB"
)

cursor = db.cursor(dictionary=True)

# =============================
# QR Code generator
# =============================
def generate_qr():
    url = "http://127.0.0.1:5000"
    qr = qrcode.make(url)
    qr_path = os.path.join("static", "qr.png")
    qr.save(qr_path)

# =============================
# DEFAULT ROUTE → LOGIN PAGE
# =============================
@app.route("/")
def home():
    return redirect(url_for("login"))

# =============================
# LOGIN PAGE
# =============================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["user_id"]
            session["user_name"] = user["name"]

            return redirect(url_for("home_page"))
        else:
            return "Invalid Email or Password"

    return render_template("login.html")

# =============================
# SIGNUP PAGE
# =============================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        hashed_password = generate_password_hash(password)

        try:
            cursor.execute(
                "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
                (name, email, hashed_password)
            )
            db.commit()

            return redirect(url_for("login"))

        except Exception as e:
            return f"Error: {str(e)}"

    return render_template("signup.html")

# =============================
# LOGOUT
# =============================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =============================
# HOME PAGE (PROTECTED)
# =============================
@app.route("/home")
def home_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    cursor.execute("""
        SELECT COUNT(*) as meals,
               SUM(calories) as calories
        FROM detection_history
        WHERE user_id = %s
    """, (user_id,))

    stats = cursor.fetchone()

    return render_template("home.html", stats=stats)
# =============================
# DASHBOARD (PROTECTED)
# =============================
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("dashboard.html")

# =============================
# DASHBOARD DATA API
# =============================
@app.route("/api/data")
def dashboard_data():
    user_id = session.get("user_id", 1)

    # ================= WEEKLY DATA =================
    cursor.execute("""
        SELECT DAYNAME(detected_at) as day, SUM(calories) as total
        FROM detection_history
        WHERE user_id=%s 
        AND YEARWEEK(detected_at,1)=YEARWEEK(CURDATE(),1)
        GROUP BY day
    """, (user_id,))

    weekly_raw = cursor.fetchall()

    # map days → index
    days_map = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2,
        "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
    }

    weekly = [0] * 7

    for row in weekly_raw:
        weekly[days_map[row["day"]]] = int(row["total"] or 0)

        # ================= WEEKLY TOTAL =================
        weekly_total = sum(weekly)

        # ================= WEEKLY MEALS =================
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM detection_history
            WHERE user_id=%s
            AND YEARWEEK(detected_at,1)=YEARWEEK(CURDATE(),1)
        """, (user_id,))
        weekly_meals = cursor.fetchone()["total"]

        # ================= STREAK =================
        cursor.execute("""
            SELECT COUNT(DISTINCT DATE(detected_at)) as streak
            FROM detection_history
            WHERE user_id=%s
            AND detected_at >= CURDATE() - INTERVAL 7 DAY
        """, (user_id,))
        streak = cursor.fetchone()["streak"]

        # TODAY CALORIES
        cursor.execute("""
            SELECT SUM(calories) as total
            FROM detection_history
            WHERE user_id=%s AND DATE(detected_at)=CURDATE()
        """, (user_id,))
        today = int(cursor.fetchone()["total"] or 0)

        # TOTAL MEALS
        cursor.execute("""
            SELECT COUNT(*) as total 
            FROM detection_history 
            WHERE user_id=%s
        """, (user_id,))
        total_meals = cursor.fetchone()["total"]

        # RECENT MEALS (WITH IMAGE)
        cursor.execute("""
            SELECT detected_food, calories, detected_at, image_path
            FROM detection_history
            WHERE user_id=%s
            ORDER BY detected_at DESC
            LIMIT 5
        """, (user_id,))

        meals = cursor.fetchall()

        # ================= NUTRIENTS ================================
        cursor.execute("""
            SELECT 
                SUM(f.protein) AS protein,
                SUM(f.carbs) AS carbs,
                SUM(f.fats) AS fats
            FROM detection_history d
            LEFT JOIN foods f ON d.food_id = f.food_id
            WHERE d.user_id = %s
        """, (user_id,))

        nutrients = cursor.fetchone()

        protein = float(nutrients["protein"] or 0)
        carbs = float(nutrients["carbs"] or 0)
        fats = float(nutrients["fats"] or 0)


    return jsonify({

        "today_calories": today,
        "goal": 2000,
        "total_meals": total_meals,
        "weekly": weekly,  # 👈 NEW
        "weekly_total": weekly_total,
        "weekly_meals": weekly_meals,
        "streak": streak,
        # 🔥 ADD THIS (MAIN FIX)
        "protein": protein,
        "carbs": carbs,
        "fats": fats,
        "meals": [
            {
                "name": m["detected_food"],
                "cal": m["calories"],
                "time": m["detected_at"].strftime("%H:%M"),
                "image": "/" + m["image_path"].replace("\\", "/")  # 👈 IMPORTANT
            }
            for m in meals
        ]
    })
# =============================
# PREDICT ROUTE
# =============================
@app.route("/predict", methods=["POST"])
def predict():
    try:
        user_id = session.get("user_id", 1)  # fallback user

        # SAVE IMAGE
        if "image" in request.files:
            file = request.files["image"]
            filename = str(int(time.time())) + "_" + file.filename
            filepath = os.path.join("static", filename)
            file.save(filepath)
        else:
            data = request.get_json()
            image_data = data["image"].split(",")[1]
            image_bytes = base64.b64decode(image_data)

            filename = str(int(time.time())) + "_capture.png"
            filepath = os.path.join("static", filename)

            with open(filepath, "wb") as f:
                f.write(image_bytes)

        # 🔥 MODEL SAFE
        try:
            food_name, confidence = predict_food(filepath)

            mapping = {
                "pizza": "Cheese Pizza",
                "burger": "Veg Burger",
                "apple": "Apple",
                "banana": "Banana",
                "cake": "Chocolate Cake",
                "fries": "French Fries",
                "noodles": "Noodles",
                "sandwich": "Veg Sandwich",
                "salad": "Salad",
                "steak": "Grilled Steak"
            }

            food_name = mapping.get(food_name, food_name)
        except Exception as e:
            print("MODEL ERROR:", e)
            food_name = "apple"   # temporary fallback
            confidence = 0.5

        confidence = round(float(confidence) * 100, 2)

        # 🔥 DB SAFE
        cursor.execute(
            "SELECT * FROM foods WHERE LOWER(food_name) = %s",
            (food_name.lower(),)
        )
        food = cursor.fetchone()

        if food:
            calories = food.get("calories", 100)
            protein = food.get("protein", 10)
            carbs = food.get("carbs", 20)
            fat = food.get("fats", 5)
            food_id = food.get("food_id", None)

            vitamin_a = food.get("vitamin_a", 0)
            calcium = food.get("calcium", 0)
            iron = food.get("iron", 0)
        else:
            calories = 100
            protein = 10
            carbs = 20
            fat = 5
            food_id = None

        # SAVE HISTORY
        cursor.execute("""
            INSERT INTO detection_history 
            (food_id, detected_food, calories, image_path, user_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (food_id, food_name, calories, filepath, user_id))

        db.commit()

        return jsonify({
            "name": food_name,
            "confidence": confidence,
            "calories": calories,
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
            "vitamin_a": vitamin_a,
            "calcium": calcium,
            "iron": iron,
            "image": "/" + filepath
        })

    except Exception as e:
        print("FULL BACKEND ERROR:", e)
        return jsonify({"error": str(e)})
# =============================
# PROFILE (PROTECTED)
# =============================
@app.route("/profile")
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()

    return render_template("profile.html", user=user)

# =============================
# UPDATE PROFILE
# =============================
@app.route("/update-profile", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return jsonify({"status": "error"})

    user_id = session["user_id"]
    data = request.json

    height = data.get("height")
    weight = data.get("weight")
    age = data.get("age")
    gender = data.get("gender")

    cursor.execute("""
        UPDATE users 
        SET height=%s, weight=%s, age=%s, gender=%s 
        WHERE user_id=%s
    """, (height, weight, age, gender, user_id))

    db.commit()

    return jsonify({"status": "success"})

# =============================
# HISTORY PAGE (PROTECTED)
# =============================
from datetime import date

@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    filter_type = request.args.get("filter")

    # ================= FILTER LOGIC =================
    if filter_type == "today":
        cursor.execute("""
                SELECT * FROM detection_history
                WHERE user_id = %s AND DATE(detected_at) = CURDATE()
                ORDER BY detected_at DESC
            """, (user_id,))
    else:
        cursor.execute("""
                SELECT * FROM detection_history
                WHERE user_id = %s
                ORDER BY detected_at DESC
            """, (user_id,))

    history_data = cursor.fetchall()
    cursor.execute("""
        SELECT * FROM detection_history
        WHERE user_id = %s
        ORDER BY detected_at DESC
    """, (user_id,))

    history_data = cursor.fetchall()

    if not history_data:
        return render_template(
            "history.html",
            history=[],
            highest=None,
            message="No meals yet. Start tracking 🍽"
        )

    # ✅ TODAY DATE
    today = date.today()

    # ✅ ADD meal_type + day_type ALWAYS
    for item in history_data:
        dt = item['detected_at']

        # 🔥 MEAL TYPE
        hour = dt.hour
        if 5 <= hour < 12:
            item['meal_type'] = 'breakfast'
        elif 12 <= hour < 17:
            item['meal_type'] = 'lunch'
        else:
            item['meal_type'] = 'dinner'

        # 🔥 DAY TYPE
        if dt.date() == today:
            item['day_type'] = 'today'
        elif (today - dt.date()).days == 1:
            item['day_type'] = 'yesterday'
        else:
            item['day_type'] = 'old'

    # ✅ HIGHEST MEAL
    highest_meal = max(history_data, key=lambda x: x['calories'])

    # ✅ TOTAL CALORIES
    total_cal = sum(item['calories'] for item in history_data)

    # ✅ MESSAGE
    if total_cal < 1200:
        message = "You're eating light today 🥗"
    elif total_cal < 2000:
        message = "Balanced intake 👍"
    else:
        message = "High calorie intake today ⚠️"

    return render_template(
        "history.html",
        history=history_data,
        highest=highest_meal,
        message=message
    )

@app.route("/delete/<int:id>", methods=["POST"])
def delete_item(id):
    print("Deleting ID:", id)

    try:
        cursor.execute(
            "DELETE FROM detection_history WHERE history_id = %s",
            (id,)
        )
        db.commit()

        return jsonify({"success": True})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"success": False})


# =============================
# FAVORITE FUNCTIONALITY
# =============================
@app.route("/favorite/<int:id>", methods=["POST"])
def favorite_item(id):
    try:
        cursor.execute("""
            UPDATE detection_history
            SET is_favorite = NOT is_favorite
            WHERE history_id = %s
        """, (id,))
        db.commit()

        return jsonify({"success": True})

    except Exception as e:
        print("FAVORITE ERROR:", e)
        return jsonify({"success": False})


# =============================
# Export Functionality
# =============================
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from flask import send_file
import io

@app.route("/export")
def export_data():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    cursor.execute("""
        SELECT detected_food, calories, detected_at
        FROM detection_history
        WHERE user_id = %s
    """, (user_id,))

    data = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Food History"

    # HEADERS
    headers = ["Food", "Calories", "Date"]
    ws.append(headers)

    # STYLE HEADERS
    for col in ws[1]:
        col.font = Font(bold=True)
        col.alignment = Alignment(horizontal="center")

    # DATA
    for row in data:
        ws.append([
            row["detected_food"],
            row["calories"],
            row["detected_at"].strftime("%d-%m-%Y %H:%M")
        ])

    # AUTO WIDTH
    for col in ws.columns:
        max_length = max(len(str(cell.value)) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max_length + 5

    # SAVE TO MEMORY
    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    return send_file(
        file_stream,
        as_attachment=True,
        download_name="Food_History.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# =============================
# Clear all Functionality
# =============================
@app.route("/clear_all", methods=["POST"])
def clear_all():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "User not logged in"})

    try:
        user_id = session["user_id"]

        # 🔥 FETCH DATA BEFORE DELETE
        cursor.execute(
            "SELECT * FROM detection_history WHERE user_id = %s",
            (user_id,)
        )
        old_data = cursor.fetchall()

        # 🔥 STORE IN SESSION (convert to list of dicts)
        session["backup_data"] = [dict(row) for row in old_data]

        # 🔥 DELETE DATA
        cursor.execute(
            "DELETE FROM detection_history WHERE user_id = %s",
            (user_id,)
        )
        db.commit()

        return jsonify({"success": True, "message": "Data cleared"})

    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)})

@app.route("/undo", methods=["POST"])
def undo():
    if "backup_data" not in session:
        return jsonify({"success": False, "message": "Nothing to undo"})

    data = session["backup_data"]

    user_id = session["user_id"]

    for row in data:
        cursor.execute(
            """
            INSERT INTO detection_history 
            (food_id, detected_food, calories, image_path, user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                row.get("food_id"),
                row.get("detected_food"),
                row.get("calories"),
                row.get("image_path"),
                user_id
            )
        )

    db.commit()

    # 🔥 Clear backup after undo
    session.pop("backup_data", None)

    return jsonify({"success": True, "message": "Undo successful"})


print(app.url_map)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)