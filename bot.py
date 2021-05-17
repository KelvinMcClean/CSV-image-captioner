#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Title2ImageBot
Complete redesign of titletoimagebot with non-deprecated apis

Always striving to improve this bot, fix bugs that crash it, and keep pushing forward with its design.
Contributions welcome

Written and maintained by CalicoCatalyst

"""
import argparse
import configparser
import curses
import logging
import os
import re
import sqlite3
import threading
import time
from io import BytesIO
from math import ceil
from os import remove

import praw
import praw.exceptions
import praw.models
import prawcore
# Use my custom fork until https://github.com/Damgaard/PyImgur/pull/43 is merged.
# pip3 install git+https://github.com/CalicoCatalyst/PyImgur
import pyimgur
import requests
from PIL import Image, ImageSequence, ImageFont, ImageDraw
from bs4 import BeautifulSoup
from gfypy import gfycat
# noinspection PyProtectedMember
from pyimgur.request import ImgurException
from requests import HTTPError

import messages

__author__ = 'calicocatalyst'
# [Major, e.g. a complete source code refactor].[Minor e.g. a large amount of changes].[Feature].[Patch]
__version__ = '1.1.3.0'


class TitleToImageBot(object):
    """Class for the bot itself.

    Attributes:
        config (Configuration): Bot Configuration Object
        reddit (praw.Reddit): Reddit client object
        imgur (PyImgur.Imgur): Imgur client object
        gfycat (Gfycat.gfycat): Gfycat client object
        screen (CLI): CLI interface object
        killthreads (bool): Setting this to true will kill any active threads
    """
    def __init__(self, config, database, screen):
        """Create the bot object

        Args:
            config (Configuration): Bot configuration API object
            database (BotDatabase): Bot database API object
            screen (CLI): Bot CLI API object
        """
        # The conifugration has all of the usernames/passwords/keys.
        self.config = config

        # Ask for our API objects from the config
        self.reddit = self.config.auth_reddit_from_config()
        self.imgur = self.config.get_imgur_client_config()
        self.gfycat = self.config.get_gfycat_client_config()
        self.screen = screen

        # get our custom BotDatabase object
        self.database = database
        self.killthreads = False
        self.thread = None

    def call_checks(self, limit):
        """Call the functions that check mentions and subs for requests

        Args:
            limit (int): How many posts back should be checked.
        """

        # Set the curses (console) text
        self.screen.set_current_action_status('Checking Mentions', "")
        # Log it to the file
        logging.info("Checking Mentions")
        #######################
        #   CHECK MENTIONS    #
        #######################
        self.check_mentions_for_requests(limit)

        # Same stuff but for our listed auto-reply subs
        self.screen.set_current_action_status('Checking Autoreply Subs', "")
        logging.info("Checking Autoreply Subs")

        #######################
        #     CHECK SUBS      #
        #######################
        self.check_subs_for_posts(limit)

    def read_comment_stream_for_manual_mentions(self):
        """Read a comment stream to check for all mentions of the old titletoimagebot

        Returns:

        """

        #######################
        #  START STREAM LOOP  #
        #######################
        for comment in self.reddit.subreddit('all').stream.comments():

            if 'u/titletoimagebot' in comment.body.lower() and comment.author.name is not 'Title2ImageBot':

                #######################
                #   PROCESS SUBMISS   #
                #######################
                processed = self.process_submission(comment.submission, comment, None,
                                                    dm=False, request_body=comment.body, customargs=[])
                if processed is not None:
                    processed_url = processed[0]
                    processed_submission = processed[1]
                    processed_source_comment = processed[2]
                    # processed_custom_title_exists = processed[3]
                    #######################
                    #        REPLY        #
                    #######################
                    self.reply_imgur_url(processed_url, processed_submission, processed_source_comment,
                                         None, customargs=[])
                else:
                    pass

            if self.killthreads:
                break

    def start_comment_streaming_thread(self):
        """Start up the comment streaming thread
        """
        #######################
        #   CALL STREAM MTHD  #
        #######################
        self.screen.set_stream_status("Active")
        thread = threading.Thread(target=self.read_comment_stream_for_manual_mentions, args=())
        thread.daemon = True
        thread.start()
        self.thread = thread
        # self.screen.set_stream_status("Active")

    def stop_comment_streaming_thread(self):
        """Stop the comment streaming thread

        """
        self.killthreads = True
        self.screen.set_stream_status("Disconnected")
        # curses.echo()
        # curses.nocbreak()
        # curses.endwin()

    def check_mentions_for_requests(self, post_limit=10):
        """Check the bot inbox for username mentions / PMs

        Args:
            post_limit (int): How far back in the inbox should we look.

        """
        # A majority of this is for the progress bar
        iteration = 1
        # Start the progress bar before we make the request.
        line = CLI.get_progress_line(iteration, post_limit)
        self.screen.set_current_action_status("Checking Inbox for requests", line)

        #######################
        #  START CHECK LOOP   #
        #######################
        for message in self.reddit.inbox.all(limit=post_limit):
            # If we're on the first one, show the progress bar not moving so we dont go over 100%
            if iteration is 1:
                # Add an iteration to the progress bar
                iteration = iteration + 1
                # Get our "Line" aka progress bar
                line = CLI.get_progress_line(1, post_limit + 1)
                # Send the line to curses (live console) with the included action.
                self.screen.set_current_action_status("Checking Inbox for requests", line)
            else:
                iteration = iteration + 1
                line = CLI.get_progress_line(iteration, post_limit + 1)
                self.screen.set_current_action_status("Checking Inbox for requests", line)

            # This is the actual function
            # noinspection PyBroadException
            try:
                # Actually send the item in the inbox to the method to process it.

                #######################
                #     PROCESS MSG     #
                #######################
                self.process_message(message)

            except Exception as ex:
                # Broad catch to prevent freak cases from crashing program.
                logging.info("Could not process %s with exception %s" % (message.id, ex))

    def check_subs_for_posts(self, post_limit=25):
        """Check autoprocess subs for posts that meet set requirements for sub

        Args:
            post_limit: How far back in the sub should we check

        """
        # Get list of subs from the config
        subs = self.config.get_automatic_processing_subs()

        # Caluclate the total amount of posts to be parsed
        totalits = len(subs) * post_limit
        iters = 0
        for sub in subs:
            # Subreddit Object for API interaction
            subr = self.reddit.subreddit(sub)

            # Grab posts from /new in sub to check
            for post in subr.new(limit=post_limit):
                iters += 1
                line = CLI.get_progress_line(iters, totalits)

                # Update curses
                self.screen.set_current_action_status("Checking Subs for posts", line)

                # If we've already parsed, skip this post iteration.
                if self.database.submission_exists(post.id):
                    continue

                title = post.title

                # does this sub have list of keywords in the title that trigger the bot on this sub
                has_triggers = self.config.configfile.has_option(sub, 'triggers')
                # does this sub have an upvote-before-parsing threshold
                has_threshold = self.config.configfile.has_option(sub, 'threshold')

                if has_triggers:
                    # Get our list of triggers.
                    triggers = str(self.config.configfile[sub]['triggers']).split('|')
                    # Skip if the title doesnt have one, but mark it as parsed.
                    if not any(t in title.lower() for t in triggers):
                        logging.debug('Title %s doesnt appear to contain any of %s, adding to parsed and skipping'
                                      % (title, self.config.configfile[sub]["triggers"]))
                        self.database.submission_insert(post.id, post.author.name, title, post.url)
                        continue
                else:
                    # No triggers so keep moving
                    logging.debug('No triggers were defined for %s, not checking' % sub)

                if has_threshold:
                    # Get the karma threshold
                    threshold = int(self.config.configfile[sub]['threshold'])
                    if post.score < threshold:
                        logging.debug('Threshold not met, not adding to parsed, just ignoring')
                        continue
                    else:
                        logging.debug('Threshold met, posting and adding to parsed')
                else:
                    logging.debug('No threshold for %s, replying to everything :)' % sub)

                #######################
                #   PROCESS SUBMISS   #
                #######################
                processed = self.process_submission(post, None, None)

                if processed is not None:
                    processed_url = processed[0]
                    processed_submission = processed[1]
                    processed_source_comment = processed[2]
                    # processed_custom_title_exists = processed[3]

                    #######################
                    #        REPLY        #
                    #######################
                    self.reply_imgur_url(processed_url, processed_submission, processed_source_comment,
                                         None, customargs=None)
                else:
                    if self.database.submission_exists(post.id):
                        continue
                    else:
                        self.database.submission_insert(post.id, post.author.name, title, post.url)
                        continue
                if sub == "TitleToImageBotSpam":
                    for comment in processed[1].comments.list():
                        if isinstance(comment, praw.models.MoreComments):
                            # See praw docs on MoreComments
                            continue
                        if not comment or comment.author is None:
                            # If the comment or comment author was deleted, skip it
                            continue
                        if comment.author.name == self.reddit.user.me().name and \
                                "Image with added title" in comment.body:
                            comment.mod.distinguish(sticky=True)

                if self.database.submission_exists(post.id):
                    continue
                else:
                    self.database.submission_insert(post.id, post.author.name, title, post.url)

    def process_message(self, message):
        """Process a detected username mention / DM

        Args:
            message (praw.models.Comment):  Message to process
        """
        if not message.author:
            return

        message_author = message.author.name
        subject = message.subject.lower()
        body_original = message.body
        body = message.body.lower()

        # Check if this message was already parsed. If so, dont parse it.
        if self.database.message_exists(message.id):
            logging.debug("bot.process_message() Message %s Already Parsed, Returning", message.id)
            return

        # Respond to the SCP Bot that erroneously detects "SCP-2" in every post.
        # There are two.
        if (message_author.lower() == "the-paranoid-android") or (message_author.lower() == "the-noided-android"):
            message.reply("Thanks Marv")
            logging.debug("Thanking marv")
            self.database.message_insert(message.id, message_author, message.subject.lower(), body)
            return

        # Skip Messages Sent by Bot
        if message_author == self.reddit.user.me().name:
            logging.debug('Message was sent, returning')
            return

        # Live Management by Bot Maintainer
        if message_author.lower() == self.config.maintainer.lower():
            if "!eval" in body:
                eval(body[5:])
            if "!del" in body or "!delete" in body:
                message.parent().delete()
            if "!edit" in body:
                message.parent().edit(body[5:])
            if "!append" in body:
                message.parent().edit(message.parent().body + body[7:])

        # Process the typical username mention
        if (isinstance(message, praw.models.Comment) and
                (subject == 'username mention' or
                 (subject == 'comment reply' and 'u/%s' % (self.config.bot_username.lower()) in body))):

            if message.author.name.lower() == 'automoderator':
                message.mark_read()
                return

            match = re.match(r'.*u/%s\s*["“”](.+)["“”].*' % (self.config.bot_username.lower()),
                             body_original, re.RegexFlag.IGNORECASE)
            title = None
            if match:
                title = match.group(1)
                if len(title) > 512:
                    title = None
                else:
                    logging.debug('Found custom title: %s', title)

            if message.submission.subreddit.display_name not in self.config.get_automatic_processing_subs() and \
                    body is not None:
                customargs = []

                dark_mode_triggers = ["!dark", "!darkmode", "!black", "!d"]
                center_mode_triggers = ["!center", "!middle", "!c"]
                auth_tag_triggers = ["!author", "tagauthor", "tagauth", "!a"]

                # If we find any apparent commands include them
                if any(x in body for x in dark_mode_triggers):
                    customargs.append("dark")
                if any(x in body for x in center_mode_triggers):
                    customargs.append("center")
                if any(x in body for x in auth_tag_triggers):
                    customargs.append("tagauth")
            else:
                customargs = []

            #######################
            #   PROCESS SUBMIS.   #
            #######################
            processed = self.process_submission(message.submission, message, title,
                                                dm=False, request_body=body, customargs=customargs)
            if processed is not None:
                processed_url = processed[0]
                processed_submission = processed[1]
                processed_source_comment = processed[2]
                # processed_custom_title_exists = processed[3]

                #######################
                #        REPLY        #
                #######################
                self.reply_imgur_url(processed_url, processed_submission, processed_source_comment,
                                     title, customargs=customargs)
            else:
                pass

            message.mark_read()

        # Process feedback and send it to bot maintainer
        elif subject.startswith('feedback'):
            self.reddit.redditor(self.config.maintainer).message("Feedback from %s" % message_author, body)
            # mark short good/bad bot comments as read to keep inbox clean
        elif 'good bot' in body and len(body) < 12:
            logging.debug('Good bot message or comment reply found, marking as read')
            message.mark_read()
        elif 'bad bot' in body and len(body) < 12:
            logging.debug('Bad bot message or comment reply found, marking as read')
            message.mark_read()

        # BETA Private Messaging Parsing feature

        pm_process_triggers = ["add", "parse", "title", "image"]

        if any(x in subject for x in pm_process_triggers):
            re1 = '.*?'  # Non-greedy match on filler
            re2 = '((?:http|https)(?::\\/{2}[\\w]+)(?:[\\/|\\.]?)(?:[^\\s"]*))'  # HTTP URL 1

            rg = re.compile(re1 + re2, re.IGNORECASE | re.DOTALL)
            m = rg.search(body)
            if m:
                http_url = m.group(1)
            else:
                return
            submission = self.reddit.submission(url=http_url)

            match = re.match(r'.*%s\s*["“”](.+)["“”].*' % http_url,
                             body_original, re.RegexFlag.IGNORECASE)
            title = None
            if match:
                title = match.group(1)
                if len(title) > 512:
                    title = None
                else:
                    logging.debug('Found custom title: %s', title)

            #######################
            #   PROCESS PM SUBM   #
            #######################
            parsed = self.process_submission(submission, None, title, True, request_body=body_original)

            processed = parsed
            if processed is not None:
                processed_url = processed[0]
                # noinspection PyUnusedLocal
                processed_submission = processed[1]
                # noinspection PyUnusedLocal
                processed_source_comment = processed[2]
                processed_custom_title_exists = processed[3]
                custom_title = processed_custom_title_exists
                upscaled = False
            else:
                #######################
                #   FALLBACK REPLIES  #
                #######################
                self.reddit.redditor(message_author).message("Sorry, I wasn't able to process that. This feature is in"
                                                             "beta and the conversation has been forwarded to the bot"
                                                             "author to see if a fix is possible.")
                self.reddit.redditor(self.config.maintainer).message("Failed to process DM request. Plz investigate")
                return
            comment = messages.PM_reply_template.format(
                image_url=processed_url,
                warntag="PM Processing is in beta!",
                custom="custom " if custom_title else "",
                nsfw="(NSFW)" if submission.over_18 else '',
                upscaled=' (image was upscaled)\n\n' if upscaled else '',
                submission_id=submission.id
            )
            #######################
            #    REPLY TO USER    #
            #######################
            self.reddit.redditor(message_author).message('Re: ' + subject, comment)
            message.mark_read()

        # Check if the bot has processed already, if so we dont need to do anything. If it hasn't,
        # add it to the database and move on
        if self.database.message_exists(message.id):
            logging.debug("bot.process_message() Message %s Already Parsed, no need to add", message.id)
            return
        else:
            self.database.message_insert(message.id, message_author, subject, body)

    # noinspection PyUnusedLocal
    def process_submission(self, submission, source_comment, title, dm=None, request_body=None, customargs=None):
        """Send info to process_image_submission and handle errors that arise from that method.

        Args:
            submission (praw.models.Submission): Post to process
            source_comment (praw.models.Comment): Comment that summoned bot
            title (str): Title to add to the image
            dm (Optional[Any]): Unused variable
            request_body (str): Unusued; Body of the request
            customargs (list[str]): Custom arguments
        """

        #######################
        #    MAIN FUNCTION    #
        #######################
        url = self.process_image_submission(submission=submission, custom_title=title, customargs=customargs)

        #######################
        #   DATABASE CHECKS   #
        #######################
        if url is None:

            self.screen.set_current_action_status('URL returned as none.', "")
            logging.debug('Checking if Bot Has Already Processed Submission')
            # This should return if the bot has already replied.
            for comment in submission.comments.list():
                if isinstance(comment, praw.models.MoreComments):
                    # See praw docs on MoreComments
                    continue
                if not comment or comment.author is None:
                    # If the comment or comment author was deleted, skip it
                    continue
                if comment.author.name == self.reddit.user.me().name and 'Image with added title' in comment.body:
                    if source_comment:
                        self.redirect_to_comment(source_comment, comment, submission)

            # If there is no comment (automatic sub parsing) and the post wasn't deleted, and its not in the table, put
            #   it in. This was a very specific issue and I'm not sure what the exact problem was, but this fixes it :)
            if (source_comment is None and
                    submission is not None and
                    not self.database.submission_exists(submission.id)):
                self.database.submission_insert(submission.id, submission.author.name, submission.title,
                                                submission.url)
                return
            # Dont parse if it's already been parsed
            if self.database.message_exists(source_comment.id):
                return
            else:
                self.database.message_insert(source_comment.id, source_comment.author.name, "comment reply",
                                             source_comment.body)
                return

        ######################
        #       RETURN       #
        ######################
        custom_title_exists = True if title is not None else False
        return [url, submission, source_comment, custom_title_exists]

    def redirect_to_comment(self, source_comment, comment, submission):
        """If a user isn't the first to ask for the bot to process, redirect them to the first asker

        Args:
            source_comment (praw.models.Comment): Comment that is currently asking
            comment (praw.models.Comment): Comment to redirect said user to
            submission (praw.models.Submission): Submission the post is on (for link generation purposes)

        """
        com_url = messages.comment_url.format(postid=submission.id, commentid=comment.id)
        reply = messages.already_responded_message.format(commentlink=com_url)

        try:
            #######################
            #    REPLY TO USER    #
            #######################
            source_comment.reply(reply)
        except prawcore.exceptions.Forbidden:
            try:
                source_comment.reply(reply)
            except prawcore.exceptions.Forbidden:
                logging.error("Failed to redirect user to comment")
            except praw.exceptions.APIException:
                logging.error("Failed to redirect user because user's comment was deleted")
            except Exception as ex:
                logging.critical("Failed to redirect user to comment with %s" % ex)
        self.database.message_insert(source_comment.id, comment.author.name, "comment reply", source_comment.body)

    # noinspection PyUnusedLocal
    def process_image_submission(self, submission, custom_title=None, commenter=None, customargs=None):
        """Process an image submission

        Args:
            submission (praw.models.Submission): Submission to process
            custom_title (str): Custom title to be potentially added
            commenter (str): Name of the person who commented. I dont think this is ever used
            customargs (list[str]): Custom arguments to process

        Returns:
            Imgur URL
        """
        if customargs:
            pls = ''.join(customargs)
        else:
            pls = ""

        if custom_title:
            parsed = self.database.submission_exists(submission.id + custom_title + pls)
        else:
            parsed = self.database.submission_exists(submission.id + pls)

        subreddit = submission.subreddit.display_name

        if parsed:
            #######################
            #         BAIL        #
            #######################
            return None

        # Make sure author account exists
        if submission.author is None:
            self.database.submission_insert(submission.id, "deletedPost", submission.title, submission.url)
            #######################
            #         BAIL        #
            #######################
            return None

        sub = submission.subreddit.display_name
        url = submission.url
        if custom_title is not None:
            title = custom_title
        else:
            title = submission.title
        submission_author = submission.author.name

        # We need to verify everything is good to go
        # Check every item in this list and verify it is 'True'
        # If the submission has been parsed, throw false which will not allow the Bot
        #   To post.

        if parsed:
            #######################
            #         BAIL        #
            #######################
            return None

        if url.endswith('.gif') or url.endswith('.gifv'):
            # Lets try this again.
            # noinspection PyBroadException
            try:

                #######################
                #    PROCESS GIFS     #
                #######################
                return self.process_gif(submission)
            except Exception as ex:
                logging.warning("gif upload failed with %s" % ex)
                #######################
                #         BAIL        #
                #######################
                return None

        # Attempt to grab the images
        try:
            response = requests.get(url)
            img = Image.open(BytesIO(response.content))
        except (OSError, IOError) as error:
            logging.warning('Converting to image failed, trying with <url>.jpg | %s', error)
            try:
                response = requests.get(url + '.jpg')
                img = Image.open(BytesIO(response.content))
            except (OSError, IOError) as error:
                logging.error('Converting to image failed, skipping submission | %s', error)
                #######################
                #         BAIL        #
                #######################
                return None
        except Exception as error:
            logging.error(error)
            logging.error('Exception on image conversion lines.')
            #######################
            #         BAIL        #
            #######################
            return None
        # noinspection PyBroadException
        try:
            image = RedditImage(img)
        except Exception as error:
            logging.error('Could not create RedditImage with %s' % error)
            #######################
            #         BAIL        #
            #######################
            return None

        if subreddit == "boottoobig":
            boot = True
        else:
            boot = False

        # I absolutely hate this method, would much rather just test length but people on StackOverflow bitch so
        if not customargs:
            image.add_title(title, boot)
        else:
            image.add_title(title=title, boot=boot, customargs=customargs, author=submission_author)

        imgur_url = self.upload(image)

        return imgur_url

    def process_gif(self, submission):
        """Process a gif.

        Notes:
            This is ineffecient and awful. I need to either research and get familiar with animated picture processing
            in python or call up on the expertise of someone experienced in the area; Also considering building a
            library to do so in a better/more suitable language and calling it as a subprocess, which would work great
            as well

            See my GfyPy project for information on how that library works.

        Args:
            submission (praw.models.Submission): Submission to process

        Returns:
            Gfycat URL
        """

        # TODO: hotfix framerate issues

        # sub = submission.subreddit.display_name
        url = submission.url
        title = submission.title
        # author = submission.author.name

        # If its a gifv and hosted on imgur, we're ok, anywhere else I cant verify it works
        if 'imgur' in url and url.endswith("gifv"):
            # imgur will give us a (however large) gif if we ask for it
            # thanks imgur <3
            url = url.rstrip('v')
        # Reddit Hosted gifs are going to be absolute hell, served via DASH which
        #       Can be checked through a fallback url :)
        try:
            response = requests.get(url)
        # The nature of this throws tons of exceptions based on what users throw at the bot
        except Exception as error:
            logging.error(error)
            logging.error('Exception on image conversion lines.')
            return None

        img = Image.open(BytesIO(response.content))
        frames = []

        # Process Gif
        # We do this by creating a reddit image for every frame of the gif
        # This is godawful, but the impact on performance isn't too bad

        # Loop over each frame in the animated image
        frame_durations = self.find_frame_durations(img)
        for frame in ImageSequence.Iterator(img):
            # Draw the text on the frame

            # We'll create a custom RedditImage for each frame to avoid
            #      redundant code

            r_frame = RedditImage(frame)
            r_frame.add_title(title, False)

            frame = r_frame.image
            # However, 'frame' is still the animated image with many frames
            # It has simply been seeked to a later frame
            # For our list of frames, we only want the current frame

            # Saving the image without 'save_all' will turn it into a single frame image, and we can then re-open it
            # To be efficient, we will save it to a stream, rather than to file
            b = BytesIO()
            frame.save(b, format="GIF")
            frame = Image.open(b)

            # The first successful image generation was 150MB, so lets see what all
            #       Can be done to not have that happen

            # Then append the single frame image to a list of frames
            frames.append(frame)
        # Save the frames as a new image
        path_gif = 'temp.gif'
        # path_mp4 = 'temp.mp4'
        frames[0].save(path_gif, save_all=True, append_images=frames[1:], duration=frame_durations)
        # ff = ffmpy.FFmpeg(inputs={path_gif: None},outputs={path_mp4: None})
        # ff.run()

        # noinspection PyBroadException
        try:
            ########################
            #   UPLOAD TO GFYCAT   #
            ########################
            url = self.upload_to_gfycat(path_gif).url
            remove(path_gif)
        except Exception as ex:
            logging.error('Gif Upload Failed with %s, Returning' % ex)
            remove(path_gif)
            return None
        # remove(path_mp4)
        return url

    def find_frame_durations(self, img):
        duration_list = list()
        img.seek(0)  # move to the start of the gif, frame 0
        # run a while loop to loop through the frames
        while True:
            try:
                frame_duration = img.info['duration']  # returns current frame duration in milli sec.
                duration_list.append(frame_duration)
                # now move to the next frame of the gif
                img.seek(img.tell() + 1)  # image.tell() = current frame
            except EOFError:
                return duration_list


    @staticmethod
    def get_params_from_twitter(link):
        """ Get the paramaters that we shove into process_image_submission from a twitter link.

        Unfinished

        TODO: REWORK HOW PROCESS_IMAGE_SUBMISSION WORKS TO ALLOW IT TO NOT NEED TO USE PRAW MODELS

        Args:
            link:

        Returns:

        """
        page = requests.get(link)
        soup = BeautifulSoup(page.text, 'html.parser')
        tweet_text = soup.select(".tweet-text")
        tweet_text_raw = tweet_text[0] if len(tweet_text) > 0 else ""
        cleaned = BeautifulSoup(str(tweet_text_raw))
        invalid_tags = ['a', 'p']
        for tag in invalid_tags:
            for match in cleaned.findAll(tag):
                match.unwrap()
        twitpiclink = 'pic.twitter.com'
        nonfluff = str(cleaned).split(twitpiclink, 1)[0]
        return [nonfluff]

    def upload(self, reddit_image):
        """
        Upload self._image to imgur

        :type reddit_image: RedditImage
        :param reddit_image:
        :returns: imgur url if upload successful, else None
        :rtype: str, NoneType
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        reddit_image.image.save(path_png)
        reddit_image.image.save(path_jpg)
        # noinspection PyBroadException
        response = None
        try:
            # Upload to imgur using pyimgur
            response = self.upload_to_imgur(path_png)
        except ImgurException as ex:
            logging.error('ImgurException: ' % ex)
            # Likely too large
            logging.warning('png upload failed with %s, trying jpg' % ex)
            try:
                # Upload to imgur using pyimgur
                response = self.upload_to_imgur(path_jpg)
            except ImgurException as ex:
                logging.error('ImgurException: %s' % ex)
                logging.error('jpg upload failed with %s, returning' % ex)
                response = None
            except HTTPError as ex:
                logging.error('HTTPError: %s' % ex)
                logging.error('jpg upload failed with %s, returning' % ex)
                response = None
        except HTTPError as ex:
            logging.error('HTTPError: %s' % ex)
            logging.error('png upload failed with %s, returning' % ex)
        finally:
            remove(path_png)
            remove(path_jpg)
        if response is None:
            return None
        return response.link

    def upload_to_imgur(self, local_image_url):
        """Upload an image to imgur from a local image url

        Make sure to use my fork of PyImgur or errors will not be raised when image upload fails. The public PyImgur
        instead prints to console when it fails.

        Args:
            local_image_url (str): Path to local file

        Returns:
            Response from imgur.

        Raises:
            ImgurException: when image upload fails for whatever reason.
        """
        # Actually call pyimgur and upload image with it
        self.screen.set_current_action_status("Uploading to Imgur...", "")
        self.screen.set_imgur_status("Uploading...")
        response = self.imgur.upload_image(local_image_url, title="Uploaded by /u/%s" % self.config.bot_username)
        self.screen.set_current_action_status("Complete", "")
        self.screen.set_imgur_status("Connected")
        return response

    def upload_to_gfycat(self, local_gif_url):
        """Upload a local gif by path to gfycat

        Args:
            local_gif_url: path to gif

        Returns:
            GfyCat object
        """
        generated_gfycat = self.gfycat.upload_file(local_gif_url)
        return generated_gfycat

    def reply_imgur_url(self, url, submission, source_comment, custom_title=None, upscaled=False, customargs=None):
        """Reply to a comment with the imgur url generated

        Args:
            url (str): URL that was generated
            submission (praw.models.Submission): Submission that was processed
            source_comment (praw.models.Comment): Comment that requested processing
            custom_title (str): Custom title if it was added
            upscaled (bool): Whether image was upscaled for processing
            customargs (list[str]): List of custom arguments if any were passed

        Returns:
            True if reply succeeded, false otherwise
        """

        self.screen.set_current_action_status('Creating reply', "")
        if submission.subreddit.display_name.lower() in self.config.get_minimal_sub_list():
            reply = messages.minimal_reply_template(
                image_url=url,
                nsfw="(NSFW)"
            )
        elif submission.subreddit.display_name.lower() == "dankmemesfromsite19":
            # noinspection PyTypeChecker
            reply = messages.site19_template.format(
                image_url=url,
                warntag="Custom titles/arguments are in beta" if customargs else "",
                custom="custom " if custom_title and len(custom_title) > 0 else "",
                nsfw="(NSFW)" if submission.over_18 else '',
                upscaled=' (image was upscaled)\n\n' if upscaled else '',
                submission_id=submission.id
            )
        elif submission.subreddit.display_name.lower() == "de":
            # noinspection PyTypeChecker
            reply = messages.de_reply_template.format(
                image_url=url,
                warntag="" if customargs else "",
                custom="anpassen " if custom_title and len(custom_title) > 0 else "",
                nsfw="(NSFW)" if submission.over_18 else '',
                upscaled=' (Das Bild wurde in der Größe geändert)\n\n' if upscaled else ''
            )
        else:
            # noinspection PyTypeChecker
            reply = messages.standard_reply_template.format(
                image_url=url,
                warntag="Custom titles/arguments are in beta" if customargs else "",
                custom="custom " if custom_title and len(custom_title) > 0 else "",
                nsfw="(NSFW)" if submission.over_18 else '',
                upscaled=' (image was upscaled)\n\n' if upscaled else '',
                submission_id=submission.id
            )
        if submission.subreddit.display_name in self.config.get_ban_sub_list():
            reply = messages.banned_PM_template.format(
                image_url=url,
                warntag="Custom titles/arguments are in beta" if customargs else "",
                custom="custom " if custom_title and len(custom_title) > 0 else "",
                nsfw="(NSFW)" if submission.over_18 else '',
                upscaled=' (image was upscaled)\n\n' if upscaled else '',
                submission_id=submission.id
            )
            # If we're banned shoot this to the sub. rest of the stuff can run, it has no effect
            source_comment.author.message("Your Title2ImageBot'd Image", reply)
        try:
            if source_comment:
                #######################
                #        REPLY        #
                #######################
                source_comment.reply(reply)
            else:
                #######################
                #     REPLY TO SUB    #
                #######################
                submission.reply(reply)
        except praw.exceptions.APIException as error:
            logging.error('Reddit api error, we\'ll try to repost later | %s', error)
            return False
        except Exception as error:
            logging.error('Cannot reply, skipping submission | %s', error)
            return False

        if customargs:
            pls = ''.join(customargs)
        else:
            pls = ""

        if custom_title:
            sid = submission.id + custom_title + pls
        else:
            sid = submission.id + pls

        self.database.submission_insert(sid, submission.author.name, submission.title, url)
        return True


class RedditImage:
    """Reddit Image class

    A majority of this class is the work of gerenook, the author of the original bot. Its ingenious work, and
    the bot absolutely could not function without it. Anything dumb here is my (CalicoCatalyst) work.
    custom arguments are my work.
    I'm going to do my best to document it.

    Attributes:
        image (Image): PIL.Image object. Once methods are ran, will contain the output as well.
        upscaled (bool): Was the image upscaled?
        title (str): Title we add to the image
    """

    margin = 10
    min_size = 500
    # font_file = 'seguiemj.ttf'
    font_file = 'roboto-emoji.ttf'
    font_scale_factor = 16
    # Regex to remove resolution tag styled as such: '[1000 x 1000]'
    regex_resolution = re.compile(r'\s?\[[0-9]+\s?[xX*×]\s?[0-9]+\]')

    def __init__(self, image):
        """Create an image object, and pass an image file to it. The RedditImage object then allows us to modify it.

        This should be an `extends Image` reeeeee

        Args:
            image (Optional[any]): Image to be processed
        """

        self.image = image
        self.upscaled = False
        self.title = ""

        width, height = image.size
        # upscale small images
        if image.size < (self.min_size, self.min_size):
            if width < height:
                factor = self.min_size / width
            else:
                factor = self.min_size / height
            self.image = self.image.resize((ceil(width * factor),
                                            ceil(height * factor)),
                                           Image.LANCZOS)
            self.upscaled = True
        self._width, self._height = self.image.size
        self._font_title = ImageFont.truetype(
            self.font_file,
            self._width // self.font_scale_factor
        )

    def __str__(self):
        """ Return the title of the image

        Returns:
            Title of the image.
        """
        return self.title

    def _split_title(self, title):
        """ Split the title into different lines for certain subreddits

        Args:
            title (str): Title to split

        Returns:
            Title that's been split

        """
        lines = ['']
        all_delimiters = [',', ';', '.']
        delimiter = None
        for character in title:
            # don't draw ' ' on a new line
            if character == ' ' and not lines[-1]:
                continue
            # add character to current line
            lines[-1] += character
            # find delimiter
            if not delimiter:
                if character in all_delimiters:
                    delimiter = character
            # end of line
            if character == delimiter:
                lines.append('')
        # if a line is too long, wrap title instead
        for line in lines:
            if self._font_title.getsize(line)[0] + RedditImage.margin > self._width:
                return self._wrap_title(title)
        # remove empty lines (if delimiter is last character)
        return [line for line in lines if line]

    def _wrap_title(self, title):
        """Actually wrap the title.

        Args:
            title: Title to wrap

        Returns:
            Wrapped title
        """
        lines = ['']
        line_words = []
        words = title.split()
        for word in words:
            line_words.append(word)
            lines[-1] = ' '.join(line_words)
            if self._font_title.getsize(lines[-1])[0] + RedditImage.margin > self._width:
                lines[-1] = lines[-1][:-len(word)].strip()
                lines.append(word)
                line_words = [word]
        # remove empty lines
        return [line for line in lines if line]

    def add_title(self, title, boot, bg_color='#fff', text_color='#000', customargs=None, author=None):
        """Add the title to an image

        return function is not used.

        Args:
            title (str): The title to add
            boot (bool): Is this a bootTooBig post
            bg_color (str): Background of the title section
            text_color (str): Foreground (text) color
            customargs (str): Custom arguments passed to the image parser
            author (str): Author of the submission

        Returns:
            Edited image
        """
        self.title = title

        title_centering = False
        dark_mode = False
        tag_author = False

        if customargs is not None:
            for arg in customargs:
                if arg == "center":
                    title_centering = True
                if arg == "dark":
                    dark_mode = True
                if arg == "tagauth":
                    tag_author = True

        if dark_mode:
            bg_color = '#000'
            text_color = '#fff'

        # remove resolution appended to title (e.g. '<title> [1000 x 1000]')
        title = RedditImage.regex_resolution.sub('', title)
        line_height = self._font_title.getsize(title)[1] + RedditImage.margin
        lines = self._split_title(title) if boot else self._wrap_title(title)
        whitespace_height = (line_height * len(lines)) + RedditImage.margin
        tagauthheight = 0
        if tag_author:
            tagauthheight = 50
        left_margin = 10
        new = Image.new('RGB', (self._width, self._height + whitespace_height + tagauthheight), bg_color)
        new.paste(self.image, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, line in enumerate(lines):
            w, h = self._font_title.getsize(line)
            left_margin = ((self._width - w) / 2) if title_centering else RedditImage.margin
            draw.text((left_margin, i * line_height + RedditImage.margin),
                      line, text_color, self._font_title)

        if tag_author:
            draw.text((left_margin, self._height + whitespace_height + self.margin),
                      author, text_color, self._font_title)
        self._width, self._height = new.size
        self.image = new
        return self.image

class Configuration(object):
    """
    Attributes:
        configfile (ConfigParser): Configuration Parser
        maintainer (str): Reddit Username of the bot maintainer.
        bot_username (str): Reddit Username of the bot
    """

    def __init__(self, config_file):
        """Interface for interacting with the bot's config file.

        Args:
            config_file (str): Name of the config file.
        """
        self._config = configparser.ConfigParser()
        self._config.read(config_file)
        self.configfile = self._config
        self.maintainer = self._config['Title2ImageBot']['maintainer']
        self.bot_username = self._config['RedditAuth']['username']

    def get_automatic_processing_subs(self):
        """Return the subreddits that should be automatically processed

        In the bot configuration file, there are configuration sections. Any other sections are subs to automatically
        parse. So here, we remove the config sections and return an array of the rest

        Returns:
            List of subs to automatically parse.

        """
        sections = self._config.sections()
        sections.remove("RedditAuth")
        sections.remove("GfyCatAuth")
        sections.remove("IgnoreList")
        sections.remove("MinimalList")
        sections.remove("ImgurAuth")
        sections.remove("Title2ImageBot")
        sections.remove("BanList")
        return sections

    def get_user_ignore_list(self):
        """Get list of users to ignore

        Typically this is bots that pester this bot (accidentally).

        Returns:
            List of usernames to ignore
        """
        ignore_list = []
        for i in self._config.items("IgnoreList"):
            ignore_list.append(i[0])
        return ignore_list

    def get_minimal_sub_list(self):
        """Get list of subs to use the minimal response format on

        Tons of issues with long responses, links to usernames, formatted links, on some subs.

        Returns:
            List of minimal-format subs

        """
        minimal_list = []
        for i in self._config.items("MinimalList"):
            minimal_list.append(i[0])
        return minimal_list

    def get_ban_sub_list(self):
        """Get list of subs to ignore completely

        Returns:
            List of subs to ignore

        """
        ban_list = []
        for i in self._config.items("BanList"):
            ban_list.append(i[0])
        return ban_list

    def get_gfycat_client_config(self):
        """Generate the gfycat client with the info in the config

        Returns:
            Gfycat client

        """
        client_id = self._config['GfyCatAuth']['publicKey']
        client_secret = self._config['GfyCatAuth']['privateKey']
        username = self._config['GfyCatAuth']['username']
        password = self._config['GfyCatAuth']['password']
        client = gfycat.GfyCatClient(client_id, client_secret, username, password)
        return client

    def auth_reddit_from_config(self):
        """Generate the reddit client with the info in the config

        Returns:
            praw Reddit object

        """
        return (praw.Reddit(client_id=self._config['RedditAuth']['publicKey'],
                            client_secret=self._config['RedditAuth']['privateKey'],
                            username=self._config['RedditAuth']['username'],
                            password=self._config['RedditAuth']['password'],
                            user_agent=self._config['RedditAuth']['userAgent']))

    def get_imgur_client_config(self):
        """Generate the imgur client from the info in the config

        Returns:
            PyImgur client.

        """
        return pyimgur.Imgur(self._config['ImgurAuth']['publicKey'])


class BotDatabase(object):
    """Interface for the SQLite 3 Database that stores processed comments/submissions
    """
    def __init__(self, db_filename, interface):
        """Interface for the SQLite 3 Database that stores processed comments/submissions

        Args:
            db_filename (str): Filename of the SQLite 3 database
            interface (CLI): CLI for the bot to allow live updates.
        """
        self._interface = interface
        self._sql_conn = sqlite3.connect(db_filename, check_same_thread=False)
        self._sql = self._sql_conn.cursor()

    def cleanup(self):
        """Clean up SQL connections before quitting the program.
        """
        self._sql_conn.commit()
        self._sql_conn.close()

    def message_exists(self, message_id):
        """Check if message exists in messages table

        Args:
            message_id (str): ID of the message to check.

        Returns:
            True if it exists in the database, False otherwise.
        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM messages WHERE id=?)', (message_id,))
        self._interface.set_data_status("Connected")
        if self._sql.fetchone()[0]:
            return True
        else:
            return False

    def submission_exists(self, submission_id):
        """Check if submission exists in submissions table

        Args:
            submission_id (str): ID of submission to check

        Returns:
            True if submission exists in database, False otherwise
        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM submissions WHERE id=?)', (submission_id,))
        self._interface.set_data_status("Connected")
        if self._sql.fetchone()[0]:
            return True
        else:
            return False

    def message_parsed(self, message_id):
        """Check if message was parsed.

        Notes:
            This is unused, as there is currently not a way to set the parsed flag, which this checks.
            *However,* a message will not be added until it is parsed. In this version of the bot, it can
            be checked using the `message_exists()` function.

            This is simply a left-over function
            from the previous developer's work that I included for the sake of including it. I'll leave it in
            just in case it is ever needed in the future

        Warnings:
            Check if the passed message ID exists before calling this, as this method does not do so.

        Args:
            message_id (str): ID of message to check

        Returns:
            True if the parsed flag is set for a message, False otherwise
        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM messages WHERE id=? AND parsed=1)', (message_id,))
        self._interface.set_data_status("Connected")
        if self._sql.fetchone()[0]:
            return True
        else:
            return False

    def message_insert(self, message_id, message_author, subject, body):
        """Insert message once it has been parsed, along with some other info about it, into messages table

        Args:
            message_id (str): Message ID (main key)
            message_author (str): Author of message
            subject (str): Subject of the message, will be the type of message recieved
            body (str): Contents of the comment/message/whatever

        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('INSERT INTO messages (id, author, subject, body) VALUES (?, ?, ?, ?)',
                          (message_id, message_author, subject, body))
        self._sql_conn.commit()
        self._interface.set_data_status("Connected")

    def submission_select(self, submission_id):
        """Return a database of information about a submission from the submissions table.

        Args:
            submission_id (str): ID of the submission

        Returns:
            {
                'id': ID of the submission,
                'author': Author of the submission,
                'title': Title of the submission,
                'url': Reddit URL of the submission,
                'imgur_url': Imgur URL that the parsed submission was uploaded to,
                'retry': Retry flag (leftover, useless)
                'timestamp': Timestamp of the post.}
        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('SELECT * FROM submissions WHERE id=?', (submission_id,))
        result = self._sql.fetchone()
        self._interface.set_data_status("Connected")
        if not result:
            return None
        return {
            'id': result[0],
            'author': result[1],
            'title': result[2],
            'url': result[3],
            'imgur_url': result[4],
            'retry': result[5],
            'timestamp': result[6]
        }

    def submission_insert(self, submission_id, submission_author, title, url):
        """Insert a submission into the submission database

        Args:
            submission_id (str): ID of the submission (main key)
            submission_author (str): Author of the submission
            title (str): Title of the submission
            url (str): Reddit URL of the submission

        """
        self._interface.set_data_status("Querying...")
        """Insert submission into submissions table"""
        self._sql.execute('INSERT INTO submissions (id, author, title, url) VALUES (?, ?, ?, ?)',
                          (submission_id, submission_author, title, url))
        self._sql_conn.commit()
        self._interface.set_data_status("Connected")

    def submission_set_retry(self, submission_id, delete_message=False, message=None):
        """Set retry flag on database

        Args:
            submission_id:
            delete_message:
            message:

        Notes:
            Unused. Set retry flag when a submission isn't able to be uploaded.

        TODO:
            Actually use this

        Raises:
            TypeError: If delete_message is true, the message needs to be passed.
        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('UPDATE submissions SET retry=1 WHERE id=?', (submission_id,))
        if delete_message:
            if not message:
                raise TypeError('If delete_message is True, message must be set')
            self._sql.execute('DELETE FROM messages WHERE id=?', (message.id,))
        self._sql_conn.commit()
        self._interface.set_data_status("Connected")

    def submission_clear_retry(self, submission_id):
        """Clear retry flag on database.

        Args:
            submission_id (str): ID of the submission

        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('UPDATE submissions SET retry=0 WHERE id=?', (submission_id,))
        self._sql_conn.commit()
        self._interface.set_data_status("Connected")

    def submission_set_imgur_url(self, submission_id, imgur_url):
        """Set imgur url for a submission

        Notes:
            Not currently used.

        Args:
            submission_id (str): Submission ID
            imgur_url (str): IMGUR url to set
        """
        self._interface.set_data_status("Querying...")
        self._sql.execute('UPDATE submissions SET imgur_url=? WHERE id=?',
                          (imgur_url, submission_id))
        self._sql_conn.commit()
        self._interface.set_data_status("Connected")


class CLI(object):
    """NCurses interface for ease of management

    Attributes:
        reddituser (str): Name of the user the bot logs in with
        redditstatus (str): Status of the connection to reddit servers
        imgurstatus (str): Status of the connection to imgur servers
        datastatus (str): Status of the connection to the SQLite database
        streamstatus (str): Status of the comment psuedostream.
        loglvl (str): Logging level for the bot logs
        killflag (bool): If this is true, stuff will stop updating.
    """

    def __init__(self):
        """ncurses interface for ease of management.
        """

        self.stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        curses.curs_set(0)

        _, self.cols = self.stdscr.getmaxyx()

        self.reddituser = "Updating..."
        self.redditstatus = "Not Connected"
        self.imgurstatus = "Not Connected"
        self.datastatus = "Not Connected"
        self.streamstatus = "Not Connected"
        self.loglvl = "Debug" if (logging.getLogger().level == logging.DEBUG) else "Standard"
        self._catx = self.cols - 36
        self._caty = 1
        self.killflag = False

    def set_reddit_user(self, reddituser):
        """Set the reddit user and update CLI

        Args:
            reddituser (str): username

        """
        self.reddituser = reddituser
        self.update_bot_status_info()

    def set_reddit_status(self, redditstatus):
        """Set the status of the reddit connection and update CLI

        Args:
            redditstatus (str): status of reddit connection
        """
        self.redditstatus = redditstatus
        self.update_bot_status_info()

    def set_imgur_status(self, imgurstatus):
        """Set the status of the imgur connection and update CLI

        Args:
            imgurstatus (str): status of imgur connection
        """
        self.imgurstatus = imgurstatus
        self.update_bot_status_info()

    def set_data_status(self, datastatus):
        """Set the status of the database connection and update CLI

        Args:
            datastatus (str): status of database connection
        """
        self.datastatus = datastatus
        self.update_bot_status_info()

    def set_stream_status(self, streamstatus):
        """Set the status of the comment stream and update CLI

        Warnings:
            Do not access this method from inside a different thread. The CLI will slowly glitch out and be unusable.

        Args:
            streamstatus (str): status of stream connection
        """
        self.streamstatus = streamstatus
        self.update_bot_status_info()

    def update_bot_status_info(self):
        """Update the entire CLI view.
        """
        if self.killflag:
            # stop updating curses. I dont know whats calling this thread after things are done
            # TODO: thread for normal loop as well
            return

        self.stdscr.refresh()
        self.clear_line(0)
        self.clear_line(5)
        self.clear_line(6)
        self.clear_line(7)
        self.clear_line(8)
        self.clear_line(9)
        self.stdscr.addstr(0, 0, "Title2ImageBot Version %s by CalicoCatalyst" % __version__)
        self.stdscr.addstr(5, 0, "Reddit Username : %s" % self.reddituser)
        self.stdscr.addstr(6, 0, "Reddit Status   : %s" % self.redditstatus)
        self.stdscr.addstr(7, 0, "Imgur Status    : %s" % self.imgurstatus)
        self.stdscr.addstr(8, 0, "Comment Stream  : %s" % self.streamstatus)
        self.stdscr.addstr(9, 0, "Database Status : %s" % self.datastatus)
        self.stdscr.addstr(10, 0, "Logging Mode    : %s" % self.loglvl)
        self.print_cat(self._catx, self._caty)
        self.stdscr.refresh()

    @staticmethod
    def get_progress_line(iteration, total, prefix='', suffix='', decimals=1, bar_length=25):
        """Generate a progress line. Credit to some guy on stackoverflow, lost the link

        Args:
            iteration (int): Iteration out of total
            total (int): Total amount of iterations
            prefix (str): Prefix of the string
            suffix (str): Suffix of the string
            decimals (int): How many decimals to count
            bar_length (int): Length of characters for bar

        Returns:
            The progress bar created.
        """

        str_format = "{0:." + str(decimals) + "f}"
        percents = str_format.format(100 * (iteration / float(total)))
        filled_length = int(round(bar_length * iteration / float(total)))
        bar = '+' * filled_length + '-' * (bar_length - filled_length)

        return '%s|%s| %s%s %s' % (prefix, bar, percents, '%', suffix)

    def set_current_action_status(self, action, statusline):
        """Set the current action and status of said action

        Args:
            action (str): Action currently being ran by the program
            statusline (str): Progress bar (generated with `get_progress_line`
        """
        self.clear_line(14)
        self.clear_line(15)
        self.stdscr.addstr(14, 0, "%s" % action)
        self.stdscr.addstr(15, 0, "%s" % statusline)
        self.print_cat(self._catx, self._caty)
        self.stdscr.refresh()

    def clear_line(self, y):
        """Clear a line in the CLI, then reprint the entire cat.

        Args:
            y (int): line to clear

        """
        self.stdscr.move(y, 0)
        self.stdscr.clrtoeol()
        self.print_cat(self._catx, self._caty)
        self.stdscr.refresh()

    def print_cat(self, startx, starty):
        """Print a cat in curses and a little credit line.

        Warnings:
            Warning: this cat is very cute

        Args:
            startx (int): Distance from the left where the cat should start
            starty (int): Distance from the top where the cat should start
        """
        if self.cols < 66:
            return
        line1 = '                      (`.-,\')'
        line2 = '                    .-\'     ;'
        line3 = '                _.-\'   , `,-'
        line4 = '          _ _.-\'     .\'  /._'
        line5 = '        .\' `  _.-.  /  ,\'._;)'
        line6 = '       (       .  )-| ('
        line7 = '        )`,_ ,\'_,\'  \_;)'
        line8 = '(\'_  _,\'.\'  (___,))'
        line9 = ' `-:;.-\''
        lines = [line1, line2, line3, line4, line5, line6, line7, line8, line9]

        num = 0

        for i in range(starty, starty + 9):
            self.stdscr.addstr(i, startx, lines[num])
            num += 1
        self.stdscr.addstr(starty+11, startx-15, 'Reddit Bot Interactive CLI by CalicoCatalyst')


def main():
    parser = argparse.ArgumentParser(description='Bot To Add Titles To Images')
    parser.add_argument('-d', '--debug', help='Enable Debug Logging', action='store_true')
    parser.add_argument('-l', '--loop', help='Enable Looping Function', action='store_true')
    parser.add_argument('limit', help='amount of submissions/messages to process each cycle',
                        type=int)
    parser.add_argument('interval', help='time (in seconds) to wait between cycles', type=int)

    args = parser.parse_args()

    current_timestamp = str(time.time())
    os.rename("logs/latest.log", "logs/logfile-" + current_timestamp + ".log")

    # Turn on debug mode with -d flag
    if args.debug:
        logging.basicConfig(filename="logs/latest.log", format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S',
                            level=logging.DEBUG)
    else:
        logging.basicConfig(filename="logs/latest.log", format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S',
                            level=logging.INFO)

    # Status line
    # TODO: Add this info to curses
    # logging.info('Bot initialized, processing the last %s submissions/messages every %s seconds' % (args.limit,
    #                                                                                                args.interval))

    # Set up CLI with curses

    interface = CLI()

    interface.update_bot_status_info()

    # Set up database
    configuration = Configuration("config.ini")
    database = BotDatabase("t2ib.sqlite", interface)

    # Begin making our CLI screen

    bot = TitleToImageBot(configuration, database, interface)

    # Status testing and stuff for the CLI

    # Get username. This will also let us know if we cant connect at all. thanks praw author guy.
    # noinspection PyBroadException
    try:
        interface.set_reddit_user(bot.reddit.user.me().name)
        interface.set_reddit_status("Connected")
    except Exception as ex:
        interface.set_reddit_user("Could not connect")
        interface.set_reddit_status("Unable to authenticate with %s" % ex)

    # This will test our connection to imgur servers by attempting to get an image I know exists. \
    # noinspection PyBroadException
    try:
        bot.imgur.get_image('S1jmapR')
        interface.set_imgur_status("Connected")
    except Exception as ex:
        interface.set_imgur_status("Unable to connect with %s" % ex)

    # This will test our connection to the database by getting a post i put in it for this purpose
    # When creating a new database, you should add a submission titled "aaaaaa" for this purpose.
    # TODO: find a less clunky method of doing this.
    # noinspection PyBroadException
    try:
        database.submission_exists("aaaaaa")
        interface.set_data_status("Connected")
    except Exception as ex:
        interface.set_data_status("Unable to connect. Yikes. With %s" % ex)

    # logging.debug('Debug Enabled')

    # noinspection PyBroadException
    try:
        if not args.loop:
            bot.call_checks(args.limit)
            interface.set_current_action_status('Checking Complete, Exiting Program', "")
            exit(0)

        bot.start_comment_streaming_thread()
        while True:
            bot.call_checks(args.limit)
            interface.set_current_action_status('Checking Complete', "")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        # WARNING: THIS WILL NOT WORK WITH FOREVER.SH FOR WHATEVER DUMB REASON
        print("Command line debugging enabled")
        # TODO: Clean this up w/ curse

        bot.stop_comment_streaming_thread()
        curses.echo()
        curses.nocbreak()
        curses.endwin()
        interface.killflag = True
        #os.system('stty sane')
        #os.system('clear')
        print('Interface and threads killed. Command line debugging enabled. Non-threaded functions still active\n')
        print('Ctrl+C again to end the program.\n')
        maxfails = 5
        fails = 0
        while True:
            try:
                command = input(">>>    ")
            except KeyboardInterrupt:
                break
            except EOFError:
                if fails == 5:
                    break
                fails += 1
                continue
            if str(command) == "quit":
                break
            try:
                exec(command)
            except Exception as ex:
                print("Failed with exception %s\n" % ex)
        logging.debug("KeyboardInterrupt Detected, Cleaning up and exiting...")
        print("Cleaning up and exiting...")
        database.cleanup()

        curses.echo()
        curses.nocbreak()
        curses.endwin()
        os.system('stty sane')
        os.system('clear')
        os.system('clear')
        exit(0)

    except Exception as ex:
        bot.reddit.redditor(bot.config.maintainer).message("bot crash", "Bot Crashed :p %s" % ex)
        curses.echo()
        curses.nocbreak()
        curses.endwin()


if __name__ == '__main__':
    main()
