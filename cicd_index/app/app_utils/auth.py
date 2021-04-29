import flask
import os
from flask.templating import render_template
from flask import redirect
import flask_login
from flask import request
from .. import app
from .. import login_manager
from .models import User

class User(flask_login.UserMixin):
    id = ""
    is_authenticated = False


@login_manager.user_loader
def user_loader(email):
    # if email not in users:
    #     return

    user = User()
    user.id = email
    user.is_authenticated = True
    return user

@app.route('/logout')
def logout():
    flask_login.logout_user()
    return 'Logged out'

@app.route('/login', methods=['GET'])
def login():
    if request.method == 'GET':
        return render_template(
            'login.html',
        )

@app.route('/login', methods=['POST'])
def login_post():
    email = flask.request.form['username']
    if flask.request.form['password'] == os.getenv("PASSWD"):
        user = User()
        user.id = email
        flask_login.login_user(user)
        return flask.redirect('/')
    return flask.redirect(flask.url_for('login'))
    
@login_manager.unauthorized_handler
def unauthorized_handler():
    return login()