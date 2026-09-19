import pymysql

# Django's MySQL backend validates minimum mysqlclient version.
# When using PyMySQL as MySQLdb, expose a compatible mysqlclient-like version.
pymysql.version_info = (2, 2, 1, "final", 0)
pymysql.__version__ = "2.2.1"
pymysql.install_as_MySQLdb()

