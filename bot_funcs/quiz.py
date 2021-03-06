import os
import random as r
from datetime import datetime
from time import sleep

import matplotlib
import numpy as np
from PIL import Image, ImageDraw
from matplotlib import patheffects
from matplotlib import pyplot as plt
from matplotlib.cbook import get_sample_data
from matplotlib.offsetbox import (OffsetImage, AnnotationBbox)
from telegram import Poll, ParseMode, Update
from telegram.ext import CallbackContext
from telegram.utils.helpers import mention_html

from constants import group_ids
from helpers.logger import logger
from helpers.namer import get_nick
from online import quiz_scraper

cwd = os.getcwd()


def send_quiz(context: CallbackContext) -> None:
    """
    Sends 5 quizzes to target chat (tms group for now). Also sets a timer for 24 hours for quiz expiry (using jobs).
    """

    right_now = datetime.now()  # returns: Datetime obj
    if 'last_quiz' not in context.bot_data:
        context.bot_data['last_quiz'] = right_now

    diff = right_now - context.bot_data['last_quiz']
    logger(message=f"Last quiz was sent {diff.days} days ago.")

    if diff.days < 7:
        print("Not enough days for next quiz!")
        return

    context.bot_data['sent_quizzes'] = []

    starts = ("See I'm keeping one quizizz now okay. You have one day to finish. For boards ok. I want everyone to do "
              "it that's it.", "I have kept one quizizz now. I expect something okay.",
              "Because of the bad like you say situation I have kept this online quizizz now. Do fast okay.",
              "I'm sending these 5 questions now like. I want it to be done by tomorrow okay? Fast fast")

    context.bot.send_message(chat_id=group_ids['grade12'], text=r.choice(starts))

    context.bot.send_chat_action(chat_id=group_ids['grade12'], action='typing')

    # Get our questions, choices and answers from the web-
    questions, choices, answers = quiz_scraper.quiz_maker_v2(number=5)

    # Support sending quiz to tms group only for now-
    for question, choice, answer in zip(questions, choices, answers):
        quiz = context.bot.send_poll(chat_id=group_ids['grade12'], question=question, options=choice,
                                     is_anonymous=False, type=Poll.QUIZ, correct_option_id=answer, is_closed=False)

        context.bot_data['sent_quizzes'].append(quiz)

    logger(message=f"The 5 quizzes were just sent to tms group successfully.")

    if right_now.day not in (29, 30, 31):  # If not in final days of a month, set date 2 days after
        context.bot_data['stop_quiz_date'] = datetime(right_now.year, right_now.month, right_now.day + 2)

    else:  # TODO: Contingency for new year
        context.bot_data['stop_quiz_date'] = datetime(right_now.year, right_now.month + 1, 1)

    context.bot_data['last_quiz'] = right_now  # Save new time for last quiz
    context.dispatcher.persistence.flush()


def timedout(context: CallbackContext) -> None:
    """Closes quiz when the time limit is over. Also scolds people if they got 3 or more answers wrong in the quiz."""

    right_now = datetime.now()  # returns: Datetime obj

    if context.bot_data['stop_quiz_date'] is None or not right_now >= context.bot_data['stop_quiz_date']:
        logger(message="not time yet (to send leaderboard)!")
        return

    to_scold = []
    scolds = ("See if this is troubling you, you can come and get help from me directly okay?",
              "Now I didn't expect thaaat level. See this is counted for the term exam okay",
              "This is for you okay? This is for you to see your level. Aim to hit the tarjit",
              "It's not that hard I expected something but I didn't know this level",
              "You have to write retest no other option like you say",
              "I'm just trying to find you option keep in mind you have any other option keep in mind like",
              "This is like you say embarrassing to me. You have to put effort and work towards the boards now",
              "That's it. I am telling mudassir sir now. Just tell me what's the confusion.",
              "Are you fine? Physics is easy what's the problem like",
              "You are troubling me. See I just wanted to be in the right direction nothing else I mean okay?")

    scold_names = ""

    for quiz in context.bot_data['sent_quizzes']:  # Stop all quizzes
        try:
            context.bot.stop_poll(chat_id=group_ids['grade12'], message_id=quiz.message_id)
        except Exception as e:
            print(e)
            pass
    context.bot.send_chat_action(chat_id=group_ids['grade12'], action='upload_photo')
    pp(context)
    leaderboard(context)  # Make the leaderboard

    context.bot.send_photo(chat_id=group_ids['grade12'], photo=open('leaderboard.png', 'rb'),
                           caption="Current standings now")  # Send latest leaderboard

    logger(message=f"The leaderboard was just sent on the group.")

    # Get user mentions of people who got 3 or more answers wrong and scold them-
    for user_id, value in context.bot_data['quizizz'].items():
        if value['answers_wrong'] >= 3:
            name = value['name']
            to_scold.append((user_id, name))  # Add to list of people to scold
        value['answers_wrong'] = 0  # Reset answers_wrong for every quiz

    for _id, name in to_scold:
        mention = mention_html(user_id=_id, name=name)  # Get their mention in html
        scold_names += mention + " "  # Add a whitespace after every name
        logger(message=f"{name} is going to be scolded.")

    if to_scold:  # Send only if there is someone to scold!
        context.bot.send_chat_action(chat_id=group_ids['grade12'], action='typing')
        sleep(2)
        context.bot.send_message(chat_id=group_ids['grade12'], text=scold_names + r.choice(scolds),
                                 parse_mode=ParseMode.HTML)

    context.bot_data['stop_quiz_date'] = None
    context.bot_data['sent_quizzes'].clear()  # Clear all quizzes

    logger(message="Changed quiz date to None and cleared sent quizzes")

    context.dispatcher.persistence.flush()


def receive_answer(update: Update, context: CallbackContext) -> None:
    """
    Saves quiz related user data. Runs everytime a user answers a quiz. This data is used later in generating the
    leaderboard.
    """

    user = update.poll_answer.user
    chosen_answer = update.poll_answer.option_ids

    # Get quiz id and correct option id-
    for quiz in context.bot_data['sent_quizzes']:
        if quiz.poll.id == update.poll_answer.poll_id:
            correct_answer = quiz.poll.correct_option_id
            logger(message=f"tms quiz was answered by {user.first_name}")
            break
    else:  # Only happens when /quizizz quiz was answered.
        logger(message=f"/quizizz was answered by {user.first_name}")
        return

    assert correct_answer is not None

    # Storing quiz related user data-
    if 'quizizz' not in context.bot_data:
        context.bot_data['quizizz'] = {}

    if user.id not in context.bot_data['quizizz']:
        # Note: `answers_wrong` below is only for one quiz. For the next quiz, they are reset.
        context.bot_data['quizizz'][user.id] = {'answers_right': 0, 'questions_answered': 0, 'answers_wrong': 0}

    # Update/add entries if changed-
    lad = context.bot_data['quizizz'][user.id]

    lad['name'] = get_nick(update, context)
    lad['profile_pic'] = f"profile_pics/{get_nick(update, context)}.jpg"
    lad['questions_answered'] += 1

    if correct_answer != chosen_answer[0]:  # If guy got it wrong
        lad['answers_wrong'] += 1
    else:
        lad['answers_right'] += 1

    context.dispatcher.persistence.flush()


def pp(context: CallbackContext) -> None:
    """Helper function to get a user's profile pic. This will be then used in the bar graph."""

    for user_id, value in context.bot_data['quizizz'].items():
        pic = context.bot.get_user_profile_photos(user_id=user_id, offset=0, limit=1)

        if not pic.photos:  # If user doesn't have a pp
            value['profile_pic'] = "profile_pics/nobody.jpg"
            continue

        first_pic = pic.photos[0][0]
        file_id = first_pic.file_id

        file = context.bot.get_file(file_id=file_id, timeout=15)  # Need a long timeout as it can take time to dl it.
        file.download(custom_path=value['profile_pic'])  # Dl's as jpg

    context.dispatcher.persistence.flush()


def round_pic() -> None:
    """
    Helper function to crop all the images in `profile_pics` into circular ones since it looks better.
    Receives files in .jpg format and saves it in .png format.
    """

    # Open the input image as numpy array, convert to RGB
    for name in os.listdir(f"{cwd}/profile_pics"):

        if name in ("nobody.png", "trophy.png"):  # We don't want to touch these, they're already round
            continue

        img = Image.open(f"profile_pics/{name}").convert("RGB")
        npImage = np.array(img)
        h, w = img.size

        # Create same size alpha layer with circle
        alpha = Image.new('L', img.size, 0)
        draw = ImageDraw.Draw(alpha)
        draw.pieslice([0, 0, h, w], 0, 360, fill=255)

        # Convert alpha Image to numpy array
        npAlpha = np.array(alpha)

        # Add alpha layer to RGB
        npImage = np.dstack((npImage, npAlpha))

        png_name = name.replace('jpg', 'png')
        jpg_name_path = f"{cwd}/profile_pics/{name}"

        # Save with alpha
        Image.fromarray(npImage).save(f"profile_pics/{png_name}")  # Only saves in .png
        os.remove(jpg_name_path)  # Remove jpg file


def add_image(name: str, x: float or int, y: float or int, offset: float, zoom: float = 0.20) -> AnnotationBbox:
    """
    Adds the given image to the bar graph, with the given specifications.

    Args:
        name - Should be a string representing name of the file to open (without file extension)
        x - x-coordinate
        y - y-coordinate
        offset - By how much to the left or right should the image be placed. Is applied only to x coordinate.
        zoom - Controls how big the image is.
    """
    # Open image as numpy array-
    try:
        pic_file = get_sample_data(f"{cwd}/profile_pics/{name}.png")
    except FileNotFoundError:  # When user has no profile pic, or changed their dp privacy settings
        pic_file = get_sample_data(f"{cwd}/profile_pics/nobody.png")

    with pic_file as file:
        arr_img = plt.imread(file, format='jpg')

    image_box = OffsetImage(arr_img, zoom=zoom)  # zoom changes the size of the image

    # Adds image to the provided coordinates-
    return AnnotationBbox(image_box, (x + offset, y), frameon=False, annotation_clip=False)


def leaderboard(context) -> None:
    """
    Makes a horizontal bar graph using data from the quiz. The list is sorted in ascending order. Thus, the person
    with the highest marks is displayed at the top. The leaderboard is then saved in the current working directory.
    """
    round_pic()  # Make sure all pics are round before starting

    names, vals = [], []

    for stuff in context.bot_data['quizizz'].values():
        names.append(stuff['name'])
        vals.append(stuff['answers_right'])

    if not names:
        return

    mean = sum(vals) / len(vals)  # Gets average for color sorting later
    vals, names = zip(*sorted(zip(vals, names)))  # Sorts both lists correspondingly in ascending order. Returns tuples
    matplotlib.use('Agg')
    canvas, ax = plt.subplots(1, 1, figsize=(10, 8))  # That fig size is perfect for 1920x1080 (Don't change this!)
    plt.grid()  # Shows grid lines

    ax.set_axisbelow(True)  # Makes grid lines go behind bars
    canvas.patch.set_facecolor("#20124d")  # Purple color as background
    ax.patch.set_facecolor("#20124d")  # and for the graph too!

    barlist = ax.barh(y=list(names), width=list(vals), height=0.4,
                      path_effects=[patheffects.SimpleLineShadow(shadow_color='#331C7C', alpha=0.8),
                                    patheffects.Normal()])  # Makes bar graph with shadows

    # for loop to adjust bar color and add arrows, correct answers and profile pics next to the bar-
    for (index, bar), name in zip(enumerate(barlist), names):

        marks = bar.get_width()  # Get no. of correct answers of that guy

        if index == len(barlist) - 1:  # Make text bolder, add trophy for the guy who is #1
            size = 15
            alpha = 1  # alpha controls transparency
            trophy_scale = 0.16 * max(vals)  # Value obtained by experimenting
            effects = [patheffects.SimpleLineShadow(shadow_color='black', alpha=0.95), patheffects.Normal()]

            ab = add_image("trophy", marks, index, offset=trophy_scale, zoom=0.034)
            ax.add_artist(ab)  # Draws annotation

        else:
            size = 11
            alpha = 0.7
            effects = None

        if marks > mean:
            color = '#00FA3F'  # Set bar color to green if guy got above avg marks
        elif marks <= mean - 5:
            color = '#FA1D07'  # Set bar color to red if guy got really bad marks
        else:
            color = '#F8ED0F'  # Set bar color to yellow if guy got below avg marks

        barlist[index].set_color(color)  # Sets bar color

        if marks != 0:  # Don't draw arrow and marks if he got a big fat ZERO.
            text_scale = 0.026 * max(vals)  # Another experimental value
            plt.text(marks - text_scale, index, str(marks), color="#000000", va='center', ha='center', alpha=alpha,
                     fontdict={'weight': 'bold', 'size': size, 'fontfamily': 'DejaVu Sans'},
                     path_effects=effects)  # Puts marks on the bars near the end

        arrow_scale = max(vals) * 0.016
        ax.annotate("", xy=(marks + arrow_scale, index), xytext=(marks + 0.001 + arrow_scale, index), xycoords='data',
                    arrowprops={'color': '#02D4F5'}, annotation_clip=False)

        # Add profile pic next to arrows-
        image_scale = max(vals) * 0.08375  # Yet another experimental value
        ab = add_image(name, marks, index, offset=image_scale)
        ax.add_artist(ab)

    # Set x ticks which are only integers, and make it aesthetically pleasing.
    plt.xticks([tick for tick in ax.get_xticks() if tick % 1 == 0], fontweight='demi', fontfamily='DejaVu Sans')
    plt.yticks(range(len(names)), names, fontweight='demi', fontstretch='condensed', fontfamily='DejaVu Sans',
               fontvariant='small-caps', fontsize=13)  # Changes look of names

    plt.ylim(top=len(vals) - 0.6)  # Slightly cut off y-axis at the top for aesthetic purposes.

    # Remove the 'box' like look of graph-
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_linewidth(False)
    ax.spines['left'].set_visible(0.9)

    # Set color to white for aesthetic purposes-
    ax.spines['left'].set_color("#FFFFFF")
    ax.spines['bottom'].set_color("#FFFFFF")

    # Change grid line properties for both x and y axis for aesthetic purposes-
    ax.tick_params(axis='x', grid_alpha=1, colors='#dcd5f4', direction='inout', grid_color='#382a65',
                   grid_linewidth=1.7)
    ax.tick_params(axis='y', colors='#dcd5f4', grid_alpha=0.0)

    # Set title and add properties to make it a beaut
    plt.title(label="LADDERBOARD",
              fontdict={'fontname': 'Gill Sans MT', 'size': 23, 'weight': 'bold', 'color': '#f3c977'}, loc='left',
              pad=20, path_effects=[patheffects.Stroke(linewidth=0.1, foreground="#F4C05B"), patheffects.Normal()])

    # Add only x axis label and then adjust it to look good.
    plt.xlabel(xlabel="Correct answers", fontdict={'size': 14, 'color': '#d6d0ec', 'weight': 'semibold'}, labelpad=18)

    plt.savefig("leaderboard.png", facecolor="#20124d")  # Save figure with same 'purple' fig color

    for name in os.listdir(f"{cwd}/profile_pics"):
        if name not in ("nobody.png", "trophy.png"):  # These should always be there
            os.remove(f"{cwd}/profile_pics/{name}")
