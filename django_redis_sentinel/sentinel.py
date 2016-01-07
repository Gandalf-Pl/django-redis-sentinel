# -*- coding: utf-8 -*-

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from redis.sentinel import Sentinel

from django_redis.client import DefaultClient

logger = logging.getLogger(__name__)


class SentinelClient(DefaultClient):
    """
    Sentinel client object extending django-redis DefaultClient
    """

    def __init__(self, server, params, backend):
        """
        Slightly different logic than connection to multiple Redis servers.
        Reserve only one write and read descriptors, as they will be closed on exit anyway.
        """
        super(SentinelClient, self).__init__(server, params, backend)
        self._client_write = None
        self._client_read = None
        self._connection_string = server

    @staticmethod
    def parse_connection_string(constring):
        """
        Parse connection string in format:
            master_name/sentinel_server:port,sentinel_server:port/db_id
        Returns master name, list of tuples with pair (host, port) and db_id
        """
        try:
            connection_params = constring.split('/')
            master_name = connection_params[0]
            servers = [host_port.split(':') for host_port in connection_params[1].split(',')]
            sentinel_hosts = [(host, int(port)) for host, port in servers]
            db = connection_params[2]
        except (ValueError, TypeError, IndexError):
            raise ImproperlyConfigured("Incorrect format '%s'" % (constring))

        return master_name, sentinel_hosts, db

    def get_client(self, write=True):
        """
        Method used to obtain a raw redis client.
        This function is used by almost all cache backend
        operations to obtain a native redis client/connection
        instance.er
        """
        logger.debug("get_client called: write=%s", write)
        if write:
            if self._client_write is None:
                self._client_write = self.connect(write=True)

            return self._client_write

        if self._client_read is None:
            self._client_read = self.connect(write=False)

        return self._client_read

    def connect(self, write=True, SentinelClass=None):
        """
        Creates a redis connection with connection pool.
        """
        if SentinelClass is None:
            SentinelClass = Sentinel
        logger.debug("connect called: write=%s", write)
        master_name, sentinel_hosts, db = \
            self.parse_connection_string(self._connection_string)

        sentinel_timeout = self._options.get('SENTINEL_TIMEOUT', 1)
        password = self._options.get('PASSWORD', None)
        sentinel = SentinelClass(sentinel_hosts,
                                 socket_timeout=sentinel_timeout,
                                 password=password)
        # 如果是写操作,则从sentinel获取对应的master读取,否则就从从库读取
        if write:
            connect = sentinel.master_for(
                master_name,
                socket_timeout=sentinel_timeout,
                db=db
            )
        else:
            connect = sentinel.slave_for(
                master_name,
                socket_timeout=sentinel_timeout,
                db=db
            )
        return connect

    def close(self, **kwargs):
        """
        虽然redis的Master和slave可能是变化的,但是sentinel.master_for会根据sentinel的
        信息来获取对应的master和slave,然后构造Redis connection
        StrictRedis<SentinelConnectionPool<service=mymaster(slave)>
        所有redis的连接不需要在每次都释放掉
        """
        logger.debug("close called")
        if getattr(settings, "DJANGO_REDIS_CLOSE_CONNECTION", False):
            if self._client_read:
                for c in self._client_read.connection_pool._available_connections:
                    c.disconnect()

            if self._client_write:
                for c in self._client_write.connection_pool._available_connections:
                    c.disconnect()

            del self._client_write
            del self._client_read
            self._client_write = None
            self._client_read = None
