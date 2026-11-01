from flask import Flask

from .board import board
from .comments import comments


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(board)
    app.register_blueprint(comments)
    return app
