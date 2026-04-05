import tensorflow as tf
import numpy as np
from tensorflow.keras.preprocessing import image

# load trained model
model = tf.keras.models.load_model("model/food_model.h5")

class_names = [
    "pizza",
    "burger",
    "apple",
    "banana",
    "cake",
    "fries",
    "noodles",
    "sandwich",
    "salad",
    "steak"
]


def predict_food(img_path):

    img = image.load_img(img_path, target_size=(224,224))
    img_array = image.img_to_array(img)

    img_array = np.expand_dims(img_array, axis=0)
    img_array = img_array / 255.0

    predictions = model.predict(img_array)

    predicted_index = np.argmax(predictions[0])
    confidence = predictions[0][predicted_index]

    predicted_food = class_names[predicted_index]

    return predicted_food, confidence