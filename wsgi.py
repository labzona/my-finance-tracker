"""PythonAnywhere WSGI entry point. Does not start a development server."""
from webapp import create_app

application = create_app()
