# Change context: `src.flask.sansio.app.App._find_error_handler`
src/flask/sansio/app.py, lines 869-892

## 1. Codebase
413 functions in 7 directories; 16 registered entry points; languages: python 413

Largest directories (functions, entry points):
- `src/flask`: 221 functions, 1 entry points
- `src/flask/sansio`: 109 functions
- `src/flask/json`: 50 functions
- `examples/tutorial/flaskr`: 18 functions, 8 entry points
- `examples/celery/src/task_app`: 11 functions, 5 entry points
- `docs`: 2 functions
- `examples/javascript/js_example`: 2 functions, 2 entry points

Strongest calls between directories:
- `src/flask` -> `src/flask/sansio`: 18 calls
- `src/flask/sansio` -> `src/flask`: 6 calls
- `examples/tutorial/flaskr` -> `src/flask/sansio`: 3 calls
- `src/flask` -> `src/flask/json`: 3 calls
- `examples/celery/src/task_app` -> `src/flask`: 2 calls
- `examples/tutorial/flaskr` -> `src/flask`: 2 calls
- `src/flask/json` -> `src/flask/sansio`: 2 calls
- `examples/celery/src/task_app` -> `src/flask/sansio`: 1 calls

## 2. Group `src/flask/sansio`
109 functions, 4 classes in 3 files.
Called from other directories (its API): `src.flask.sansio.app.App._find_error_handler` (3), `src.flask.sansio.app.App.add_url_rule` (2), `src.flask.sansio.app.App.iter_blueprints` (2), `src.flask.sansio.app.App.register_blueprint` (2), `src.flask.sansio.app.App.__init__` (1), `src.flask.sansio.app.App.create_global_jinja_loader` (1), `src.flask.sansio.app.App.handle_url_build_error` (1), `src.flask.sansio.app.App.inject_url_defaults` (1)
Depends on: `src/flask` (6 calls)
Used by: `src/flask` (18 calls), `examples/tutorial/flaskr` (3 calls), `src/flask/json` (2 calls), `examples/celery/src/task_app` (1 calls)

## 3. Anchor
```python
    def _find_error_handler(
        self, e: Exception, blueprints: list[str]
    ) -> ft.ErrorHandlerCallable | None:
        """Return a registered error handler for an exception in this order:
        blueprint handler for a specific code, app handler for a specific code,
        blueprint handler for an exception class, app handler for an exception
        class, or ``None`` if a suitable handler is not found.
        """
        exc_class, code = self._get_exc_class_and_code(type(e))
        names = (*blueprints, None)

        for c in (code, None) if code is not None else (None,):
            for name in names:
                handler_map = self.error_handler_spec[name][c]

                if not handler_map:
                    continue

                for cls in exc_class.__mro__:
                    handler = handler_map.get(cls)

                    if handler is not None:
                        return handler
        return None
```

Digest (what its code does, line by line):
```
L877 exc_class, code = self._get_exc_class_and_code(type(e)) [calls: _get_exc_class_and_code() -> src.flask.sansio.scaffold.Scaffold._get_exc_class_and_code]
L878 names = (*blueprints, None)
when for each c in (code, None) if code is not None else (None,):
  when for each name in names:
    L882 handler_map = self.error_handler_spec[name][c]
    when not handler_map:
      L885 skip to the next iteration
    when for each cls in exc_class.__mro__:
      L888 handler = handler_map.get(cls) [calls: get() -> one of src.flask.ctx._AppCtxGlobals.get|src.flask.sansio.scaffold.Scaffold.get]
      when handler is not None:
        L891 return handler
L892 return None
```

## 4. Who depends on it
- `src.flask.app.Flask.handle_exception` (src/flask/app.py)
  - L943 result: stores the result in `handler`, then checks `handler is not None`; passes it to `self.ensure_sync()`
  - L943 arguments: passes 2 positional arguments
- `src.flask.app.Flask.handle_http_exception` (src/flask/app.py)
  - L860 result: stores the result in `handler`, then checks `handler is None`; passes it to `self.ensure_sync()`
  - L860 arguments: passes 2 positional arguments
- `src.flask.app.Flask.handle_user_exception` (src/flask/app.py)
  - L890 result: stores the result in `handler`, then checks `handler is None`; passes it to `self.ensure_sync()`
  - L890 arguments: passes 2 positional arguments

## 5. What it depends on
- L877 errors: `src.flask.sansio.scaffold.Scaffold._get_exc_class_and_code` can raise `ValueError`; this function does not catch it
- L877 errors: `src.flask.sansio.scaffold.Scaffold._get_exc_class_and_code` can raise `TypeError`; this function does not catch it

## 6. Style to follow
Conventions in `src/flask/sansio`:
- errors are raised as `ValueError` (7 of 17 raises here)
- `src.flask.sansio.blueprints.Blueprint.record_once` is a shared helper here: called by 10 of 109 functions
- `src.flask.sansio.scaffold.Scaffold._method_route` is a shared helper here: called by 6 of 109 functions
- function names are snake_case (80 of 80 multi-word names)
- `@setupmethod` is used on 44 of 109 functions

Sibling `src.flask.sansio.scaffold.Scaffold.register_error_handler` (src/flask/sansio/scaffold.py:649; same directory, shares 1 callee, name words: error, handler):
```python
    @setupmethod
    def register_error_handler(
        self,
        code_or_exception: type[Exception] | int,
        f: ft.ErrorHandlerCallable,
    ) -> None:
        """Alternative error attach function to the :meth:`errorhandler`
        decorator that is more straightforward to use for non decorator
        usage.

        .. versionadded:: 0.7
        """
        exc_class, code = self._get_exc_class_and_code(code_or_exception)
        self.error_handler_spec[None][code][exc_class] = f
```

Sibling `src.flask.sansio.app.App.handle_url_build_error` (src/flask/sansio/app.py:982; same class, name words: error):
```python
    def handle_url_build_error(
        self, error: BuildError, endpoint: str, values: dict[str, t.Any]
    ) -> str:
        """Called by :meth:`.url_for` if a
        :exc:`~werkzeug.routing.BuildError` was raised. If this returns
        a value, it will be returned by ``url_for``, otherwise the error
        will be re-raised.

        Each function in :attr:`url_build_error_handlers` is called with
        ``error``, ``endpoint`` and ``values``. If a function returns
        ``None`` or raises a ``BuildError``, it is skipped. Otherwise,
        its return value is returned by ``url_for``.

        :param error: The active ``BuildError`` being handled.
        :param endpoint: The endpoint being built.
        :param values: The keyword arguments passed to ``url_for``.
        """
        for handler in self.url_build_error_handlers:
            try:
                rv = handler(error, endpoint, values)
            except BuildError as e:
                # make error available outside except block
                error = e
            else:
                if rv is not None:
                    return rv

        # Re-raise if called with an active exception, otherwise raise
        # the passed in exception.
        if error is sys.exc_info()[1]:
            raise

        raise error
```

## 7. Callers' code
`src.flask.app.Flask.handle_exception` (src/flask/app.py):
```python
    def handle_exception(self, ctx: AppContext, e: Exception) -> Response:
        """Handle an exception that did not have an error handler
        associated with it, or that was raised from an error handler.
        This always causes a 500 ``InternalServerError``.

        Always sends the :data:`got_request_exception` signal.

        If :data:`PROPAGATE_EXCEPTIONS` is ``True``, such as in debug
        mode, the error will be re-raised so that the debugger can
        display it. Otherwise, the original exception is logged, and
        an :exc:`~werkzeug.exceptions.InternalServerError` is returned.

        If an error handler is registered for ``InternalServerError`` or
        ``500``, it will be used. For consistency, the handler will
        always receive the ``InternalServerError``. The original
        unhandled exception is available as ``e.original_exception``.

        .. versionchanged:: 1.1.0
            Always passes the ``InternalServerError`` instance to the
            handler, setting ``original_exception`` to the unhandled
            error.

        .. versionchanged:: 1.1.0
            ``after_request`` functions and other finalization is done
            even for the default 500 response when there is no handler.

        .. versionadded:: 0.3
        """
        exc_info = sys.exc_info()
        got_request_exception.send(self, _async_wrapper=self.ensure_sync, exception=e)
        propagate = self.config["PROPAGATE_EXCEPTIONS"]

        if propagate is None:
            propagate = self.testing or self.debug

        if propagate:
            # Re-raise if called with an active exception, otherwise
            # raise the passed in exception.
            if exc_info[1] is e:
                raise

            raise e

        self.log_exception(ctx, exc_info)
        server_error: InternalServerError | ft.ResponseReturnValue
        server_error = InternalServerError(original_exception=e)
        handler = self._find_error_handler(server_error, ctx.request.blueprints)

        if handler is not None:
            server_error = self.ensure_sync(handler)(server_error)

        return self.finalize_request(ctx, server_error, from_error_handler=True)
```
`src.flask.app.Flask.handle_http_exception` (src/flask/app.py):
```python
    def handle_http_exception(
        self, ctx: AppContext, e: HTTPException
    ) -> HTTPException | ft.ResponseReturnValue:
        """Handles an HTTP exception.  By default this will invoke the
        registered error handlers and fall back to returning the
        exception as response.

        .. versionchanged:: 1.0.3
            ``RoutingException``, used internally for actions such as
             slash redirects during routing, is not passed to error
             handlers.

        .. versionchanged:: 1.0
            Exceptions are looked up by code *and* by MRO, so
            ``HTTPException`` subclasses can be handled with a catch-all
            handler for the base ``HTTPException``.

        .. versionadded:: 0.3
        """
        # Proxy exceptions don't have error codes.  We want to always return
        # those unchanged as errors
        if e.code is None:
            return e

        # RoutingExceptions are used internally to trigger routing
        # actions, such as slash redirects raising RequestRedirect. They
        # are not raised or handled in user code.
        if isinstance(e, RoutingException):
            return e

        handler = self._find_error_handler(e, ctx.request.blueprints)
        if handler is None:
            return e
        return self.ensure_sync(handler)(e)  # type: ignore[no-any-return]
```
`src.flask.app.Flask.handle_user_exception` (src/flask/app.py):
```python
    def handle_user_exception(
        self, ctx: AppContext, e: Exception
    ) -> HTTPException | ft.ResponseReturnValue:
        """This method is called whenever an exception occurs that
        should be handled. A special case is :class:`~werkzeug
        .exceptions.HTTPException` which is forwarded to the
        :meth:`handle_http_exception` method. This function will either
        return a response value or reraise the exception with the same
        traceback.

        .. versionchanged:: 1.0
            Key errors raised from request data like ``form`` show the
            bad key in debug mode rather than a generic bad request
            message.

        .. versionadded:: 0.7
        """
        if isinstance(e, BadRequestKeyError) and (
            self.debug or self.config["TRAP_BAD_REQUEST_ERRORS"]
        ):
            e.show_exception = True

        if isinstance(e, HTTPException) and not self.trap_http_exception(e):
            return self.handle_http_exception(ctx, e)

        handler = self._find_error_handler(e, ctx.request.blueprints)

        if handler is None:
            raise

        return self.ensure_sync(handler)(e)  # type: ignore[no-any-return]
```

## 8. Wider impact
6 transitive callers; by distance: 1 hop: 3, 2 hops: 2, 3 hops: 1
