"""Flask app factory."""
import logging

from flask import Flask

from . import db
from .config import Config


def create_app(overrides=None):
    """Build and configure the Flask app."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not app.config["MACHINE_KEY"]:
        app.logger.warning("MACHINE_KEY is empty - all machine requests will be rejected")
    if not app.config.get("SECRET_KEY_FROM_ENV", True):
        app.logger.warning("SECRET_KEY not set in .env - using a random one for this process")
    app.logger.info("Database: %s", app.config["DATABASE_PATH"])
    if app.config["PAYOUT_MODE"] == "cashfree":
        app.logger.info("Payouts: Cashfree %s", app.config["CASHFREE_ENV"])
        if not (app.config["CASHFREE_CLIENT_ID"] and app.config["CASHFREE_CLIENT_SECRET"]):
            app.logger.warning("PAYOUT_MODE=cashfree but CASHFREE_CLIENT_ID/SECRET are empty - payouts will fail")

    db.init_app(app)

    from .routes import admin, machine, user
    app.register_blueprint(admin.bp)
    app.register_blueprint(machine.bp)
    app.register_blueprint(user.bp)
    return app
