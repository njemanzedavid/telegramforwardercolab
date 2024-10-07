# config.py
import os

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', '60c42768b3650591581be13fea5f5922')
