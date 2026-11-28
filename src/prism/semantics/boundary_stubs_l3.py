"""Layer 3 boundary stubs: third-party entry points (Phase 2, Operation
Groundwire) - TEMPORARY.

`requests.post`, `psycopg2.connect`, `axios.get`, `go-redis`, ... are what
host code actually calls, and until Phase 3 indexes dependency source
there is no other way to see that such a call reaches the network or a
database. These entries keep that classification alive with exactly the
Substance category they had in the retired 639-entry catalog, mapped to
the same atomic primitive bits as Layer 2.

Delete this module once Phase 3 shows dependency indexing reaches the
Layer 2 primitives on its own; nothing else depends on it existing."""
from __future__ import annotations

L3_BOUNDARY_STUBS: dict[str, dict[str, set[str]]] = {
    "network_io": {
        "python": {
            # aiohttp
            "aiohttp.ClientSession",
            "aiohttp.ClientSession.delete",
            "aiohttp.ClientSession.get",
            "aiohttp.ClientSession.head",
            "aiohttp.ClientSession.patch",
            "aiohttp.ClientSession.post",
            "aiohttp.ClientSession.put",
            "aiohttp.ClientSession.request",
            "aiohttp.request",
            # boto3
            "boto3.client",
            "boto3.resource",
            # grpc
            "grpc.aio.insecure_channel",
            "grpc.aio.secure_channel",
            "grpc.insecure_channel",
            "grpc.secure_channel",
            # httpx
            "httpx.AsyncClient",
            "httpx.AsyncClient.request",
            "httpx.Client",
            "httpx.Client.request",
            "httpx.delete",
            "httpx.get",
            "httpx.head",
            "httpx.options",
            "httpx.patch",
            "httpx.post",
            "httpx.put",
            "httpx.request",
            # requests
            "requests.delete",
            "requests.get",
            "requests.head",
            "requests.options",
            "requests.patch",
            "requests.post",
            "requests.put",
            "requests.request",
            # urllib3
            "urllib3.PoolManager.request",
            "urllib3.PoolManager.urlopen",
            "urllib3.request",
            # websocket
            "websocket.WebSocket.connect",
            "websocket.create_connection",
            # websockets
            "websockets.connect",
        },
        "javascript": {
            # axios
            "axios",
            "axios.delete",
            "axios.get",
            "axios.head",
            "axios.options",
            "axios.patch",
            "axios.post",
            "axios.put",
            "axios.request",
            # got
            "got",
            "got.delete",
            "got.get",
            "got.patch",
            "got.post",
            "got.put",
            # node-fetch
            "node-fetch",
            "node-fetch.default",
            # undici
            "undici.fetch",
            "undici.pipeline",
            "undici.request",
            "undici.stream",
            # ws
            "ws",
            "ws.WebSocket",
        },
        "go": {
            # google.golang.org
            "google.golang.org/grpc.Dial",
            "google.golang.org/grpc.DialContext",
            "google.golang.org/grpc.NewServer",
        },
    },
    "database_io": {
        "python": {
            # asyncpg
            "asyncpg.connect",
            "asyncpg.create_pool",
            # cx_Oracle
            "cx_Oracle.connect",
            # django
            "django.db.backends.base.base.BaseDatabaseWrapper.cursor",
            "django.db.connection.cursor",
            "django.db.models.Manager.create",
            "django.db.models.QuerySet.create",
            "django.db.models.QuerySet.delete",
            "django.db.models.QuerySet.update",
            # mysql
            "mysql.connector.connect",
            "mysql.connector.connection.MySQLConnection.cursor",
            "mysql.connector.cursor.MySQLCursor.execute",
            # oracledb
            "oracledb.connect",
            # psycopg
            "psycopg.AsyncConnection.connect",
            "psycopg.Connection.execute",
            "psycopg.Cursor.execute",
            "psycopg.connect",
            # psycopg2
            "psycopg2.connect",
            "psycopg2.extensions.connection.cursor",
            # pymongo
            "pymongo.MongoClient",
            "pymongo.collection.Collection.delete_many",
            "pymongo.collection.Collection.delete_one",
            "pymongo.collection.Collection.find",
            "pymongo.collection.Collection.find_one",
            "pymongo.collection.Collection.insert_many",
            "pymongo.collection.Collection.insert_one",
            "pymongo.collection.Collection.update_many",
            "pymongo.collection.Collection.update_one",
            # pymysql
            "pymysql.connect",
            # pyodbc
            "pyodbc.connect",
            # redis
            "redis.Redis",
            "redis.Redis.delete",
            "redis.Redis.execute_command",
            "redis.Redis.expire",
            "redis.Redis.get",
            "redis.Redis.hget",
            "redis.Redis.hset",
            "redis.Redis.lpush",
            "redis.Redis.rpush",
            "redis.Redis.sadd",
            "redis.Redis.set",
            "redis.Redis.srem",
            # sqlalchemy
            "sqlalchemy.create_engine",
            "sqlalchemy.engine.Connection.exec_driver_sql",
            "sqlalchemy.engine.Connection.execute",
            "sqlalchemy.orm.Session.add",
            "sqlalchemy.orm.Session.commit",
            "sqlalchemy.orm.Session.delete",
            "sqlalchemy.orm.Session.execute",
            "sqlalchemy.orm.Session.rollback",
        },
        "javascript": {
            # better-sqlite3
            "better-sqlite3",
            "better-sqlite3.Database",
            "better-sqlite3.Database.prepare",
            # ioredis
            "ioredis",
            "ioredis.Redis",
            "ioredis.Redis.del",
            "ioredis.Redis.get",
            "ioredis.Redis.hset",
            "ioredis.Redis.lpush",
            "ioredis.Redis.rpush",
            "ioredis.Redis.set",
            # knex
            "knex",
            "knex.raw",
            # mongodb
            "mongodb.Collection.deleteMany",
            "mongodb.Collection.deleteOne",
            "mongodb.Collection.find",
            "mongodb.Collection.findOne",
            "mongodb.Collection.insertMany",
            "mongodb.Collection.insertOne",
            "mongodb.Collection.updateMany",
            "mongodb.Collection.updateOne",
            "mongodb.Db.collection",
            "mongodb.MongoClient",
            # mongoose
            "mongoose.Document.save",
            "mongoose.Model.create",
            "mongoose.Model.deleteMany",
            "mongoose.Model.deleteOne",
            "mongoose.Model.find",
            "mongoose.Model.findOne",
            "mongoose.Model.updateMany",
            "mongoose.Model.updateOne",
            "mongoose.connect",
            # mysql
            "mysql.createConnection",
            "mysql.createPool",
            # mysql2
            "mysql2.createConnection",
            "mysql2.createPool",
            "mysql2/promise.createConnection",
            "mysql2/promise.createPool",
            # pg
            "pg.Client",
            "pg.Client.query",
            "pg.Pool",
            "pg.Pool.connect",
            "pg.Pool.query",
            # prisma
            "prisma.PrismaClient",
            "prisma.PrismaClient.$executeRaw",
            "prisma.PrismaClient.$queryRaw",
            # redis
            "redis.RedisClient",
            "redis.createClient",
            # sequelize
            "sequelize",
            "sequelize.Model.create",
            "sequelize.Model.destroy",
            "sequelize.Model.findAll",
            "sequelize.Model.findOne",
            "sequelize.Model.update",
            "sequelize.Sequelize",
            # sqlite3
            "sqlite3.Database",
            "sqlite3.Database.all",
            "sqlite3.Database.exec",
            "sqlite3.Database.get",
            "sqlite3.Database.run",
            # typeorm
            "typeorm.DataSource",
            "typeorm.DataSource.getRepository",
            "typeorm.DataSource.query",
            "typeorm.Repository.delete",
            "typeorm.Repository.find",
            "typeorm.Repository.findOne",
            "typeorm.Repository.findOneBy",
            "typeorm.Repository.query",
            "typeorm.Repository.remove",
            "typeorm.Repository.save",
            "typeorm.Repository.update",
            "typeorm.createConnection",
            "typeorm.getConnection",
            "typeorm.getRepository",
        },
        "go": {
            # github.com
            "github.com/jackc/pgx.Conn.Exec",
            "github.com/jackc/pgx.Conn.Query",
            "github.com/jackc/pgx.Conn.QueryRow",
            "github.com/jackc/pgx.Connect",
            "github.com/jackc/pgx.ConnectConfig",
            "github.com/redis/go-redis/v9.Client.Del",
            "github.com/redis/go-redis/v9.Client.Get",
            "github.com/redis/go-redis/v9.Client.HSet",
            "github.com/redis/go-redis/v9.Client.LPush",
            "github.com/redis/go-redis/v9.Client.RPush",
            "github.com/redis/go-redis/v9.Client.Set",
            "github.com/redis/go-redis/v9.NewClient",
            # go.mongodb.org
            "go.mongodb.org/mongo-driver/mongo.Client.Database",
            "go.mongodb.org/mongo-driver/mongo.Collection.DeleteMany",
            "go.mongodb.org/mongo-driver/mongo.Collection.DeleteOne",
            "go.mongodb.org/mongo-driver/mongo.Collection.Find",
            "go.mongodb.org/mongo-driver/mongo.Collection.FindOne",
            "go.mongodb.org/mongo-driver/mongo.Collection.InsertMany",
            "go.mongodb.org/mongo-driver/mongo.Collection.InsertOne",
            "go.mongodb.org/mongo-driver/mongo.Collection.UpdateMany",
            "go.mongodb.org/mongo-driver/mongo.Collection.UpdateOne",
            "go.mongodb.org/mongo-driver/mongo.Connect",
            # gorm.io
            "gorm.io/gorm.DB.Create",
            "gorm.io/gorm.DB.Delete",
            "gorm.io/gorm.DB.Exec",
            "gorm.io/gorm.DB.Find",
            "gorm.io/gorm.DB.First",
            "gorm.io/gorm.DB.Raw",
            "gorm.io/gorm.DB.Save",
            "gorm.io/gorm.DB.Updates",
            "gorm.io/gorm.Open",
        },
    },
    "process_io": {
        "python": {
            # pexpect
            "pexpect.spawn",
        },
        "go": {
            # golang.org
            "golang.org/x/sys/unix.Exec",
            "golang.org/x/sys/unix.ForkExec",
        },
    },
    "randomness": {
        "python": {
            # numpy
            "numpy.random.choice",
            "numpy.random.default_rng",
            "numpy.random.rand",
            "numpy.random.randint",
            "numpy.random.random",
        },
        "go": {
            # github.com
            "github.com/gofrs/uuid.New",
            "github.com/gofrs/uuid.NewV4",
            "github.com/google/uuid.New",
            "github.com/google/uuid.NewString",
        },
    },
}
