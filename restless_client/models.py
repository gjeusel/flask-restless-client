import logging
from itertools import chain

import crayons

from .inspect import inspect
from .utils import generate_id

logger = logging.getLogger('restless-client')


def get_class(cls, kwargs, meta):
    if not meta.polymorphic.get('identities'):
        return cls
    discriminator_name = meta.polymorphic['on']
    for discriminator, kls in meta.polymorphic['identities'].items():
        if kwargs.get(discriminator_name) == discriminator:
            return meta.client._classes[kls]


class BaseObject:
    def __init__(self, **kwargs):
        oid = self.__pk_name
        super().__setattr__(oid,
                            kwargs[oid] if oid in kwargs else generate_id())
        self._rlc.deserializer.load(self, kwargs)
        self._rlc.client._register(self)

    def __new__(cls, **kwargs):
        key = None
        meta = cls._rlc
        if kwargs.get(meta.pk_name):
            key = '%s%s' % (cls.__name__, kwargs[meta.pk_name])
        if key in meta.client.registry:
            obj = meta.client.registry[key]
            logger.debug(crayons.yellow('Using existing {}'.format(key)))
        else:
            cls = get_class(cls, kwargs, meta)
            obj = object.__new__(cls)
            obj._rlc = inspect(obj)
            logger.debug(crayons.yellow('initialising {}'.format(key)))
        return obj

    def __setattr__(self, name, value):
        if not name.startswith('_'):
            if name not in chain(self._rlc._relations, self._rlc._attributes):
                raise AttributeError('{} has no attribute named {}'.format(
                    self._rlc.class_name, name))
        self._rlc.relhelper.is_valid_instance(name, value)
        object.__setattr__(self, name, value)

    @classmethod  # noqa A003
    def all(cls):
        # shorthand for Class.query.all()
        return cls.query.all()

    @classmethod
    def get(cls, oid):
        # shorthand for Class.query.get()
        return cls.query.get(oid)

    @property
    def __pk_val(self):
        return getattr(self, self._rlc.pk_name)

    @property
    def __pk_name(self):
        return self._rlc.pk_name

    def delete(self):
        self._rlc.connection.delete(self)

    def save(self):
        if self._rlc.is_new:
            self._rlc.connection.create(self)
        elif self._rlc.dirty:
            self._rlc.connection.update(self)
        else:
            logger.debug("No action needed")
        self._rlc.dirty = set()

    def __repr__(self):
        return str(self)

    def __str__(self):
        attributes = ["{}: {}".format(self.__pk_name, self.__pk_val)]
        if hasattr(self, 'name'):
            attributes.append("name: {}".format(self.name))
        return "<{} [{}]>".format(self.__class__.__name__,
                                  " | ".join(attributes))
