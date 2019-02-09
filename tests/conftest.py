import time
from multiprocessing import Process

import flask
import pytest

import flask_restless
from flask_sqlalchemy import SQLAlchemy
from restless_client import DataModel


@pytest.fixture
def app():
    app = flask.Flask(__name__)
    app.config['DEBUG'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return app


@pytest.fixture
def client(app):
    with app.app_context():
        return app.test_client


def create_webapp():
    app = flask.Flask(__name__)

    db = SQLAlchemy(app)

    class Person(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.Unicode, unique=True)
        birth_date = db.Column(db.Date)

    class Computer(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.Unicode, unique=True)
        vendor = db.Column(db.Unicode)
        purchase_time = db.Column(db.DateTime)
        owner_id = db.Column(db.Integer, db.ForeignKey('person.id'))
        owner = db.relationship(
            'Person', backref=db.backref('computers', lazy='dynamic'))

    db.create_all()

    manager = flask_restless.APIManager(app, flask_sqlalchemy_db=db)
    manager.create_api(Person, methods=['GET'], include_columns=['name'])
    manager.create_api(
        Computer,
        methods=['GET'],
        collection_name='compjutahs',
        exclude_columns=['name'])
    data_model = DataModel(manager)
    manager.create_api(data_model, methods=['GET'])

    return app


@pytest.fixture(scope='function')
def runserver(request):
    def run():
        create_webapp().run()

    p = Process(target=run)
    p.start()

    def finalize():
        p.terminate()

    request.addfinalizer(finalize)
    time.sleep(0.1)

    yield p
