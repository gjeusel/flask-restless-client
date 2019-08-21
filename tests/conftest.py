import time
import pytest
import flask_restless
from flask import Flask
from multiprocessing import Process
from flask_sqlalchemy import SQLAlchemy
from fast_alchemy import FlaskFastAlchemy
from restless_client import Client
from restless_client.ext.auth import BaseSession
from flask_restless_datamodel import DataModel
from requests_flask_adapter import Session
import os

ROOT_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(ROOT_DIR, 'data')
API_METHODS = ['GET', 'PUT', 'POST', 'DELETE']


class RaiseSession(BaseSession, Session):
    pass


def build_endpoints(app, fa):
    manager = flask_restless.APIManager(app, flask_sqlalchemy_db=app.db)
    for class_name, class_ in fa.class_registry.items():
        manager.create_api(class_, methods=API_METHODS)
        setattr(app, class_name, class_)
    data_model = DataModel(manager)
    manager.create_api(data_model, methods=['GET'])
    print(id(manager))
    app.manager = manager


@pytest.fixture(scope='function')
def app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///:memory:"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db = SQLAlchemy(app)
    app.db = db
    return app


@pytest.fixture(scope='function')
def instances(app):
    fa = FlaskFastAlchemy(app.db)
    fa.load(os.path.join(DATA_DIR, 'instances.yaml'))
    build_endpoints(app, fa)
    return app


@pytest.fixture(scope='function')
def filters(app):
    fa = FlaskFastAlchemy(app.db)
    fa.load(os.path.join(DATA_DIR, 'filter.yaml'))
    build_endpoints(app, fa)
    return app


@pytest.fixture
def cl(instances):
    RaiseSession.register('http://app', instances)
    return Client(url='http://app/api', session=RaiseSession(), debug=True)


@pytest.fixture
def fcl(filters):
    RaiseSession.register('http://app', filters)
    return Client(url='http://app/api', session=RaiseSession(), debug=True)


@pytest.fixture
def mcl(instances):
    class Apartment(instances.Formicarium):
        __mapper_args__ = {
            'polymorphic_identity': 'apartment'
        }

        def function_without_params(self):
            return 5

        def function_with_params(self, arg1, arg2):
            return '{}: {}'.format(arg1, arg2)

        def function_with_kwargs(self, kwarg1, kwarg2):
            return self.function_with_params(kwarg1, kwarg2)

        def funtion_with_args_kwargs(self, arg1, kwarg1):
            return self.function_with_params(arg1, kwarg1)

        def function_with_default_params(self, param1=5, param2=6):
            return self.function_with_params(param1, param2)

        def function_with_an_object(self, obj):
            return obj.name


    Apartment.__tablename__ = 'apartment'

    instances.db.session.add(Apartment(name='ApAntMent'))
    instances.db.session.commit()

    instances.manager.create_api(Apartment, methods=API_METHODS)

    RaiseSession.register('http://app', instances)
    return Client(url='http://app/api', session=RaiseSession(), debug=True)
