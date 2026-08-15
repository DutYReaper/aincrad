from flask import Flask, Response
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    # Возвращаем простой текст без лишних HTML-тегов
    return Response("I'm alive!", mimetype='text/plain')

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
