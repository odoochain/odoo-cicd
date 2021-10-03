import flask_login
from flask_login import login_manager

class User(flask_login.UserMixin):
    id = 0

# @login_manager.user_loader
# def user_loader(email):
#     if email not in users:
#         return

#     user = User()
#     user.id = email
#     return user