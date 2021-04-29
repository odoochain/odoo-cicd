import flask
from flask.templating import render_template
from flask import redirect
import flask_login
from flask import request
from .. import app
from .. import login_manager
from .models import User

users = ['marc@itewimmer.de']

class User(flask_login.UserMixin):
    pass


@login_manager.user_loader
def user_loader(email):
    import pudb;pudb.set_trace()
    if email not in users:
        return

    user = User()
    user.id = email
    user.is_authenticated = True
    return user


#@login_manager.request_loader
# def request_loader(request, methods=['GET', 'POST']):
#     import pudb;pudb.set_trace()
#     # if request.method == 'GET':
#     #     return render_template(
#     #         'login.html',
#     #     )

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
    if flask.request.form['password'] == '123':
        user = User()
        user.id = email
        flask_login.login_user(user)
        return flask.redirect('/')
    return flask.redirect(flask.url_for('login'))
    
@login_manager.unauthorized_handler
def unauthorized_handler():
    return login()