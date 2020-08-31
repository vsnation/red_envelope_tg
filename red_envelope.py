import json
import traceback
import random
from PIL import Image, ImageFont, ImageDraw
import matplotlib.pyplot as plt
import datetime
import time
from pymongo import MongoClient
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
import uuid

plt.style.use('seaborn-whitegrid')

with open('services.json') as conf_file:
    conf = json.load(conf_file)
    connectionString = conf['mongo']['connectionString']
    bot_token = conf['telegram_bot']['bot_token']
    dictionary = conf['dictionary']
    groth_in_beams = conf['groth_in_beams']
    point_to_pixels = conf['point_to_pixels']

bold = ImageFont.truetype(font="fonts/ProximaNova-Bold.ttf", size=int(20 * point_to_pixels))
regular = ImageFont.truetype(font="fonts/ProximaNova-Regular.ttf", size=int(20 * point_to_pixels))
bold_high = ImageFont.truetype(font="fonts/ProximaNova-Bold.ttf", size=int(28 * point_to_pixels))


class Defender:
    def __init__(self):
        # Beam Butler Initialization
        self.bot = Bot(bot_token)
        client = MongoClient(connectionString)
        db = client.get_default_database()
        self.col_users = db['Users']
        self.col_envelopes = db['envelopes']

        self.message, self.text, self._is_video, self.message_text, \
            self.first_name, self.username, self.user_id, self.beam_address, \
            self.balance_in_beam, self.locked_in_beam, self.is_withdraw, self.balance_in_groth, \
            self._is_verified, self.group_id, self.group_username = \
                None, None, None, None, None, None, None, None, None, None, None, None, None, None, None

        self.new_message = None

        while True:
            try:
                self._is_user_in_db = None
                # get chat updates
                new_messages = self.wait_new_message()
                self.processing_messages(new_messages)
            except Exception as exc:
                print(exc)

    def processing_messages(self, new_messages):
        for self.new_message in new_messages:
            try:
                time.sleep(0.1)
                self.message = self.new_message.message \
                    if self.new_message.message is not None \
                    else self.new_message.callback_query.message
                self.text, self._is_video = self.get_action(self.new_message)
                self.message_text = str(self.text).lower()
                print(self.text)
                # init user data
                self.first_name = self.new_message.effective_user.first_name
                self.username = self.new_message.effective_user.username
                self.user_id = int(self.new_message.effective_user.id)

                self.beam_address, self.balance_in_beam, self.locked_in_beam, self.is_withdraw = self.get_user_data()
                self.balance_in_groth = self.balance_in_beam * groth_in_beams if self.balance_in_beam is not None else 0

                try:
                    self._is_verified = \
                        self.col_users.find_one({"_id": self.user_id})['IsVerified']
                    self._is_user_in_db = self._is_verified
                except Exception as exc:
                    print(exc)
                    self._is_verified = True
                    self._is_user_in_db = False
                #
                print(self.username)
                print(self.user_id)
                print(self.first_name)
                print(self.message_text, '\n')
                self.group_id = self.message.chat.id
                self.group_username = self.get_group_username()

                split = self.text.split(' ')
                if len(split) > 1:
                    args = split[1:]
                else:
                    args = None

                self.action_processing(str(split[0]).lower(), args)
            except Exception as exc:
                print(exc)
                traceback.print_exc()

    def get_group_username(self):
        """
            Get group username
        """
        try:
            return str(self.message.chat.username)
        except Exception:
            return str(self.message.chat.id)


    def get_user_username(self):
        """
                Get User username
        """
        try:
            return str(self.message.from_user.username)
        except Exception:
            return None

    def wait_new_message(self):
        while True:
            updates = self.bot.get_updates(allowed_updates=["message", "callback_query"])
            if len(updates) > 0:
                break
        update = updates[-1]
        self.bot.get_updates(offset=update["update_id"] + 1, allowed_updates=["message", "callback_query"])
        return updates

    @staticmethod
    def get_action(message):
        _is_document = False
        menu_option = None

        if message['message'] is not None:
            menu_option = message['message']['text']
            _is_document = message['message']['document'] is not None
            if 'mp4' in str(message['message']['document']):
                _is_document = False

        elif message["callback_query"] != 0:
            menu_option = message["callback_query"]["data"]

        return str(menu_option), _is_document


    def action_processing(self, cmd, args):
        """
            Check each user actions
        """
        if cmd.startswith("/envelope"):
            try:
                self.bot.delete_message(self.group_id, self.message.message_id)
            except Exception:
                pass
            
            if self.message.chat['type'] == 'private':
                self.bot.send_message(
                    self.user_id,
                    "<b>You can use this cmd only in the group</b>",
                    parse_mode="html"
                )
                return 

            if not self.check_user():
                return

            if not self.is_withdraw:
                try:
                    if args is not None and len(args) == 1:
                        self.create_red_envelope(*args)
                    else:
                        self.incorrect_parametrs_image()
                except Exception as exc:
                    print(exc)
                    self.incorrect_parametrs_image()
            else:
                self.bot.send_message(
                    self.user_id,
                    "<b>You can't create envelope until transaction confirmed!!</b>",
                    parse_mode='HTML'
                )

        elif cmd.startswith("catch_envelope|"):
            if not self.check_user():
                return
            try:
                envelope_id = cmd.split("|")[1]
                self.catch_envelope(envelope_id)
            except Exception as exc:
                print(exc)
                self.incorrect_parametrs_image()

        elif cmd.startswith("/balance"):
            if not self.check_user():
                return
            self.bot.send_message(
                self.user_id,
                dictionary['balance'] % ("{0:.8f}".format(float(self.balance_in_beam)), "{0:.8f}".format(float(self.locked_in_beam))),
                parse_mode='HTML'
            )

        elif cmd.startswith("/start"):
            self.auth_user()

    def check_user(self):
        """
            Is user verified
        """
        if self._is_user_in_db:
            return True
        else:
            self.bot.send_message(self.group_id,
                                  "User is not authorized in the bot!")
            return False

    def send_message(self, user_id, text, parse_mode):
        try:
            self.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=parse_mode
            )
        except Exception as exc:
            print(exc)

    def get_user_data(self):
        """
            Get user data
        """
        try:
            _user = self.col_users.find_one({"_id": self.user_id})
            return _user['BeamAddress'], _user['Balance'], _user['Locked'], _user['IsWithdraw']
        except Exception as exc:
            print(exc)
            traceback.print_exc()
            return None, None, None, None

    def red_envelope_catched(self, amount):
        im = Image.open("images/red_envelope_catched.jpg")

        d = ImageDraw.Draw(im)
        location_transfer = (256, 71)
        location_amount = (256, 105)
        location_addess = (225, 140)

        d.text(location_transfer, "YOU CAUGHT", font=bold, fill='#FFFFFF')
        d.text(location_amount, "%s BEAM" % amount, font=bold, fill='#f72c56')
        d.text(location_addess, "FROM A RED ENVELOPE", font=regular, fill='#FFFFFF')
        image_name = 'catched.jpg'
        im.save(image_name)
        try:
            self.bot.send_photo(
                self.user_id,
                open(image_name, 'rb')
            )
        except Exception as exc:
            print(exc)

    def red_envelope_created(self, first_name, envelope_id):
        im = Image.open("images/red_envelope_created.jpg")

        d = ImageDraw.Draw(im)
        location_who = (240, 105)
        location_note = (256, 140)

        d.text(location_who, "%s CREATED" % first_name, font=bold, fill='#ffffff')
        d.text(location_note, "A RED ENVELOPE", font=bold,
               fill='#f72c56')
        image_name = 'created.jpg'
        im.save(image_name)
        try:
            response = self.bot.send_photo(
                self.group_id,
                open(image_name, 'rb'),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        text='Catch Beams✋',
                        callback_data='catch_envelope|%s' % envelope_id
                    )]]
                )
            )
            return response['message_id']
        except Exception as exc:
            print(exc)
            return 0

    def red_envelope_ended(self):
        im = Image.open("images/red_envelope_ended.jpg")

        d = ImageDraw.Draw(im)
        location_who = (256, 71)
        location_note = (306, 115)

        d.text(location_who, "RED ENVELOPE", font=bold, fill='#f72c56')
        d.text(location_note, "DEPLETED", font=bold, fill='#ffffff')
        image_name = 'depleted.png'
        im.save(image_name)
        try:
            msg = self.bot.send_photo(
                self.user_id,
                open(image_name, 'rb')
            )
            msg = self.bot.edit_message_media(
                 chat_id=self.group_id,
                 message_id=self.message.message_id,
                 reply_markup=InlineKeyboardMarkup([])
            )
            print(msg)
        except Exception as exc:
            print(exc)

    def incorrect_parametrs_image(self):
        im = Image.open("images/incorrect_parametrs_template.png")

        d = ImageDraw.Draw(im)
        location_text = (230, 52)

        d.text(location_text, "Incorrect parameters", font=bold,
               fill='#FFFFFF')

        image_name = 'incorrect_parametrs.png'
        im = im.convert("RGB")
        im.save(image_name)
        self.bot.send_photo(
            self.user_id,
            open(image_name, 'rb'),
            caption=dictionary['incorrect_parametrs'],
            parse_mode='HTML'
        )

    def insufficient_balance_image(self):
        im = Image.open("images/insufficient_balance_template.png")

        d = ImageDraw.Draw(im)
        location_text = (230, 52)

        d.text(location_text, "Insufficient Balance", font=bold, fill='#FFFFFF')

        image_name = 'insufficient_balance.png'
        im = im.convert("RGB")
        im.save(image_name)
        try:
            self.bot.send_photo(
                self.user_id,
                open(image_name, 'rb'),
                caption=dictionary['incorrect_balance'] % "{0:.8f}".format(
                    float(self.balance_in_beam)),
                parse_mode='HTML'
            )
        except Exception as exc:
            print(exc)

    def create_red_envelope(self, amount):
        try:
            amount = float(amount)

            if amount < 0.001:
                self.incorrect_parametrs_image()
                return

            if self.balance_in_beam >= amount:
                envelope_id = str(uuid.uuid4())[:8]

                self.col_users.update(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "Balance": float("{0:.8f}".format(float(self.balance_in_beam) - amount))
                            }
                    }
                )

                msg_id = self.red_envelope_created(self.first_name[:8], envelope_id)

                self.col_envelopes.insert_one(
                    {
                        "_id": envelope_id,
                        "amount": amount,
                        "remains": amount,
                        "group_id": self.group_id,
                        "group_username": self.group_username,
                        "group_type": self.message.chat['type'],
                        "creator_id": self.user_id,
                        "creator_username": self.username,
                        "msg_id": msg_id,
                        "takers": [],
                        "created_at": int(datetime.datetime.now().timestamp())
                    }
                )
            else:
                self.insufficient_balance_image()

        except Exception as exc:
            self.incorrect_parametrs_image()
            print(exc)

    def catch_envelope(self, envelope_id):
        try:
            envelope = self.col_envelopes.find_one({"_id": envelope_id})
            _is_envelope_exist = envelope is not None
            _is_ended = envelope['remains'] == 0
            _is_user_catched = str(self.user_id) in str(envelope['takers'])

            if _is_user_catched:
                self.answer_call_back(text="❗️You have already caught BEAM from this envelope❗️",
                                      query_id=self.new_message.callback_query.id)
                return

            if _is_ended:
                self.answer_call_back(text="❗RED ENVELOPE DEPLETED❗️",
                                      query_id=self.new_message.callback_query.id)
                self.red_envelope_ended()
                self.delete_tg_message(self.group_id, self.message.message_id)
                return

            if _is_envelope_exist:
                minimal_amount = 0.001
                if envelope['remains'] <= minimal_amount:
                    catch_amount = envelope['remains']
                else:
                    if len(envelope['takers']) < 5:
                        catch_amount = float(
                            "{0:.8f}".format(float(random.uniform(minimal_amount, envelope['remains'] / 2))))
                    else:
                        catch_amount = float(
                            "{0:.8f}".format(float(random.uniform(minimal_amount, envelope['remains']))))

                new_remains = float("{0:.8f}".format(envelope['remains'] - catch_amount))
                if new_remains < 0:
                    new_remains = 0
                    catch_amount = envelope['remains']

                self.col_envelopes.update_one(
                    {
                        "_id": envelope_id,
                    },
                    {
                        "$push": {
                            "takers": [self.user_id, catch_amount]
                        },
                        "$set": {
                            "remains": new_remains
                        }
                    }
                )
                self.col_users.update_one(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "Balance": float("{0:.8f}".format(float(self.balance_in_beam) + catch_amount))
                            }
                    }
                )
                try:
                    if envelope['group_username'] != "None":
                        msg_text = '<i><a href="tg://user?id=%s">%s</a> caught %s Beams from a <a href="https://t.me/%s/%s">RED ENVELOPE</a></i>' % (
                            self.user_id,
                            self.first_name,
                            "{0:.8f}".format(catch_amount),
                            envelope['group_username'],
                            envelope['msg_id']
                        )
                    else:
                        msg_text = '<i><a href="tg://user?id=%s">%s</a> caught %s Beams from a RED ENVELOPE</i>' % (
                            self.user_id,
                            self.first_name,
                            "{0:.8f}".format(catch_amount),
                        )
                    self.bot.send_message(
                        envelope['group_id'],
                        text=msg_text,
                        disable_web_page_preview=True,
                        parse_mode='HTML'
                    )
                except Exception:
                    traceback.print_exc()

                self.red_envelope_catched("{0:.8f}".format(catch_amount))

            else:
                self.insufficient_balance_image()

        except Exception as exc:
            self.incorrect_parametrs_image()
            print(exc)

    def delete_tg_message(self, user_id, message_id):
        try:
            self.bot.delete_message(user_id, message_id=message_id)
        except Exception:
            pass

    def answer_call_back(self, text, query_id):
        try:
            self.bot.answer_callback_query(
                query_id,
                text=text,
                show_alert=True
            )
        except Exception as exc:
            print(exc)

    def auth_user(self):
        try:
            if self.beam_address is None:
                if not self._is_verified:
                    self.bot.send_message(
                        self.user_id,
                        "You're successfully registered in the bot"
                    )

                    self.col_users.update_one(
                        {
                            "_id": self.user_id
                        },
                        {
                            "$set":
                                {
                                    "IsVerified": True,
                                    "Balance": 1,
                                    "Locked": 0,
                                    "IsWithdraw": False
                                }
                        }, upsert=True
                    )

                else:
                    self.col_users.update_one(
                        {
                            "_id": self.user_id
                        },
                        {
                            "$set":
                                {
                                    "_id": self.user_id,
                                    "first_name": self.first_name,
                                    "username": self.username,
                                    "IsVerified": True,
                                    "JoinDate": datetime.datetime.now(),
                                    "BeamAddress": "SomeAddress",
                                    "Balance": 1,
                                    "Locked": 0,
                                    "IsWithdraw": False,
                                }
                        }, upsert=True
                    )

                    self.bot.send_message(
                        self.user_id,
                        "You're successfully registered in the bot",
                        parse_mode='html',
                        disable_web_page_preview=True
                    )

            else:
                self.col_users.update(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "IsVerified": True,
                            }
                    }, upsert=True
                )
                self.bot.send_message(
                    self.user_id,
                    "You're successfully registered in the bot",
                    parse_mode='html',
                    disable_web_page_preview=True
                )
        except Exception as exc:
            print(exc)
            traceback.print_exc()

def main():
    try:
        Defender()
    except Exception as e:
        print(e)
        traceback.print_exc()


if __name__ == '__main__':
    main()
