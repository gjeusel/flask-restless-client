import inspect
import json
from contextlib import contextmanager
from functools import wraps
from itertools import chain

import flask
from flask import Response, abort, current_app

import flask_restless
from cereal_lazer import DynamicType, register_serializable_type
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.inspection import inspect as sqla_inspect
from webargs import fields
from webargs.flaskparser import parser as argparser


def serializer_for(model):
    class ModelSerializer(fields.String):
        def _deserialize(self, value, attr, data):
            return model.query.get(value)

        def _serialize(self, value, attr, obj):
            return value.id

    return ModelSerializer


def run_object_method(instid, function_name, model, parser_args):
    instance = model.query.get(instid)
    if not instance:
        return {}
    kwargs = argparser.parse(parser_args, flask.request)
    res = getattr(instance, function_name)(**kwargs)
    return json.dumps(DynamicType()._serialize(res, None, None))


@contextmanager
def assure_mimerender():
    """
    decorators upon decorators, this context manager is there to please the
    mimerender decorator when calling the view_func in the blueprint created
    by Flask-Restless. The mimerender decorator sets some flask environment
    variables before calling the view_func, and then unsets them after.

    We are using the view_func of each registered model blueprint to get
    relevant information about how each model is configured in Flask-Restless.
    Since we're piggy backing on the model view_funcs by calling them within the
    parent view_func, we are unsetting the context variables of the parent
    view_func when exiting the child view_func.

    This context manager will take care of the context vars handling, so that 
    the parent view_func will have vars to unset
    """
    context_keys = [
        'mimerender_shortmime', 'mimerender_mime', 'mimerender_renderer'
    ]
    context_vars = {}
    for key in context_keys:
        context_vars[key] = flask.request.environ[key]
    yield
    for key in context_keys:
        flask.request.environ[key] = context_vars.pop(key)


def catch_model_configuration(dispatch_request):
    """
    This is the actual point where we catch the relevant configuration made
    by Flask-Restless. Currently we are only interested in the include and
    exclude columns, but as needs may arise in the future this method may
    grow.

    Flask-Restless generates APIView classes on the fly for each registered model
    and uses this class as a view_func. We monkey patch the call to get access
    to the parameters and then return it back to the original method.
    Due to Flask's "as_view" implementation, it is the only entry point to
    retrieve this information without restrictions. There are other ways to
    retrieve it, but it relies on import and initialisation order, and quickly
    becomes dirty and restrictive.

    And this way, we're at least dropping the restrictive part :)
    """

    def wrapper(self, *args, **kwargs):
        def clean(columns):
            return columns or []

        include_columns = chain(
            clean(self.include_columns), clean(self.include_relations))
        exclude_columns = chain(
            clean(self.exclude_columns), clean(self.exclude_relations))
        # Putting back the old and original dispatch_request method to continue
        # normal operation from this point on.
        self.__class__.dispatch_request = dispatch_request
        return {
            'include': list(include_columns),
            'exclude': list(exclude_columns)
        }

    return wrapper


def inject_preprocessor(fn, data_model):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        model = args[0]
        app = kwargs.get('app', data_model.api_manager.app)
        if isinstance(model, DataModel):
            kwargs['preprocessors'] = data_model.processors
            data_model.register_method_urls(app)
        blueprint = fn(*args, **kwargs)
        api_info = data_model.api_manager.created_apis_for[model]
        data_model.register_method_url(model, app, api_info.collection_name)
        return blueprint

    return wrapper


class DataModel(object):
    __tablename__ = 'restless-client-datamodel'

    def __init__(self, api_manager, **options):
        """
        In Flask-Restless, it is up to you to choose which models you would like
        to make available as an api. It should also be a choice to expose your
        datamodel, and preferably in the same intuitive way that you register
        your models.

        This object functions as a puppet model to give the user the feeling 
        like they are registering just another model they want to expose.
        """
        api_manager.create_api_blueprint = inject_preprocessor(
            api_manager.create_api_blueprint, self)
        self.api_manager = api_manager
        self.data_model = {}
        self.flag_for_inheritance = {}
        self.options = options
        self.model_methods = {}

    @property
    def processors(self):
        return {
            'GET': [self.intercept_and_return_datamodel],
            'GET_MANY': [self.intercept_and_return_datamodel]
        }

    def intercept_and_return_datamodel(self, *args, **kwargs):
        """
        This method must be called as a preprocessor to the actual restless
        api call. It will construct the json data model, if it hasn't already been
        constructed, and return it.

        The goal of running this method as a preprocessor is so that we have 
        chance to intercept the request before it gets sent to do the actual
        restless database query.

        Since this model is not an actual SQLAlchemy model, it will crash when
        actual db queries are executed on it. This is why we're (mis)using the
        flask abort to prematurely break off the normal restless flow,
        as by now we have all the data we need to return our request.
        """
        if not self.data_model:
            with assure_mimerender():
                for model, api_info in self.api_manager.created_apis_for.items(
                ):
                    if model is self:
                        continue
                    kwargs = self.get_restless_model_conf(model, api_info)
                    kwargs['bp_name'] = api_info.blueprint_name
                    self.register_model(model, **kwargs)
        # update child models with the attributes and relations of their parent
        if self.flag_for_inheritance:
            for model, inheriting_from in self.flag_for_inheritance.items():
                self.resolve_inheritance(model, inheriting_from)
        # (Mis)using the flask abort to return the datamodel before the
        # request gets forwarded to the actual db querying
        abort(Response(json.dumps(self.data_model)))

    def resolve_inheritance(self, model, inheriting_from):
        for imodel in inheriting_from:
            iattrs = self.data_model[imodel]['attributes']
            irels = self.data_model[imodel]['relations']
            self.data_model[model]['attributes'].update(iattrs)
            self.data_model[model]['relations'].update(irels)

    def get_restless_model_conf(self, model, api_info):
        """
        This method will try to find the corresponding view within the registered
        blueprints in flask-restless and momentarily replace it with a function
        that is able to distil the relevant infomation we need to construct a
        datamodel that is conform to what constraints were defined in 
        flask restiless when registering models. 
        Afterwards it will replace the function handle back to its original
        function.
        """
        api_format = flask_restless.APIManager.APINAME_FORMAT
        endpoint = api_format.format('{1}.{0}'.format(*api_info))

        view_func = current_app.view_functions[endpoint]

        dispatch_fn = catch_model_configuration(
            view_func.view_class.dispatch_request)
        view_func.view_class.dispatch_request = dispatch_fn
        result = view_func().json
        return {
            'collection_name': api_info.collection_name,
            'included': result['include'],
            'excluded': result['exclude']
        }

    def register_model(self, model, collection_name, bp_name, included,
                       excluded):
        """
        Loops over all attributes, relations, hybrid properties and merges
        inherited classes with their parent and puts this information in a 
        serialisable dictionary
        """
        if model is self:
            return
        attribute_dict = {}
        foreign_keys = {}
        tbl = model.__table__

        def is_valid(column):
            column = column.split('.')[-1]
            valid_excl = True
            valid_incl = True
            if excluded:
                valid_excl = column not in excluded
            if included:
                valid_incl = column in included
            return valid_excl and valid_incl

        # attributes
        for column in tbl.columns:
            if is_valid(column.name):
                ctype = column.type.__class__.__name__.lower()
                attribute_dict[column.name] = ctype

        # relations
        for rel in sqla_inspect(model).relationships:
            if is_valid(str(rel.key)):
                foreign_keys[rel.key] = {
                    'foreign_model': rel.mapper.class_.__name__,
                    'relation_type': rel.direction.name,
                    'backref': rel.back_populates,
                }

        # hybrid
        hybrid_properties = [
            a for a in sqla_inspect(model).all_orm_descriptors
            if isinstance(a, hybrid_property)
        ]
        for attribute in hybrid_properties:
            if is_valid(attribute):
                attribute_dict[attribute.__name__] = 'hybrid'

        # inheritance
        if hasattr(model, '__mapper_args__'
                   ) and 'polymorphic_identity' in model.__mapper_args__:
            db = self.api_manager.flask_sqlalchemy_db
            inheriting_from = []
            for kls in model.__bases__:
                if issubclass(kls, db.Model):
                    inheriting_from.append(kls.__name__)
            self.flag_for_inheritance[model.__name__] = inheriting_from

        self.data_model[model.__name__] = {
            'collection_name': collection_name,
            'attributes': attribute_dict,
            'relations': foreign_keys,
            'methods': self.model_methods.get(collection_name, {})
        }

    def register_method_urls(self, app):
        for model, api_info in self.api_manager.created_apis_for.items():
            if model is self:
                continue
            self.register_method_url(model, app, api_info.collection_name)

    def register_method_url(self, model, app, collection_name):
        methods = self.compile_method_list(model)
        self.model_methods[collection_name] = methods
        self.add_method_endpoints(collection_name, model, methods, app)
        if not model is self:
            register_serializable_type(model.__name__, serializer_for(model))

    def compile_method_list(self, model):
        methods = {}
        include_internal = self.options.get('include_model_internal_functions',
                                            False)
        for name, fn in inspect.getmembers(
                model, predicate=inspect.isfunction):
            if name.startswith('__'):
                continue
            if name.startswith('_') and not include_internal:
                continue

            spec = inspect.getargspec(fn)
            # get the params
            if spec[3]:
                required = spec[0][:-len(spec[3])]
                optional = spec[0][-len(spec[3]):]
            else:
                required = spec[0]
                optional = []
            if 'self' in required:
                required.remove('self')
            methods[name] = {
                'required_params': required,
                'optional_params': optional,
            }
        return methods

    def add_method_endpoints(self, collection_name, model, methods, app):
        for method, details in methods.items():
            fmt = '/api/method/{0}/<instid>/{1}'
            instance_endpoint = fmt.format(collection_name, method)
            parser_args = {}
            for param in chain(details['required_params'],
                               details['optional_params']):
                required = param in details['required_params']
                parser_args[param] = DynamicType(required=required)
            app.add_url_rule(
                instance_endpoint,
                methods=['GET'],
                defaults={
                    'function_name': method,
                    'model': model,
                    'parser_args': parser_args
                },
                view_func=run_object_method)
