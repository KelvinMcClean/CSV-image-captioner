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
import time
from io import BytesIO
from math import ceil
from os import remove

import praw
import praw.exceptions
import praw.models
import prawcore
import pyimgur
import requests
from PIL import Image, ImageSequence, ImageFont, ImageDraw
from bs4 import BeautifulSoup
from gfypy import gfycat
# noinspection PyProtectedMember
from pip._vendor.distlib.compat import raw_input

import messages

__author__ = 'calicocatalyst'
# [Major, e.g. a complete source code refactor].[Minor e.g. a large amount of changes].[Feature].[Patch]
__version__ = '1.1.0.12'


class TitleToImageBot(object):

    def __init__(self, config, database, screen):
        """
        Set our API variables for us to access

        :param config: Configuration Object that stores a lot of variable info on how the bot runs
        :type config: Configuration
        :param database: Database that stores a list of parsed messages and submissions with some info
        :type database: BotDatabase
        """
        # The conifugration has all of the usernames/passwords/keys.
        self.config = config

        # Ask for our API objects from the config
        self.reddit = self.config.auth_reddit_from_config()
        self.imgur = self.config.get_imgur_client_config()
        self.gfycat = self.config.get_gfycat_client_config()
        self.screen = screen

        self.database = database

    def call_checks(self, limit):
        """
        Check messages, then check subreddits that have asked us to run on them.

        :param limit: How far back in our posts should we go.
        :type limit: int
        """
        self.screen.set_current_action_status('Checking Mentions', "")
        logging.info("Checking Mentions")
        self.check_mentions_for_requests(limit)
        self.screen.set_current_action_status('Checking Autoreply Subs', "")
        logging.info("Checking Autoreply Subs")
        self.check_subs_for_posts(limit)

    def check_mentions_for_requests(self, post_limit=10):
        """
        Check our inbox for any activity with PRAW and send it all to process_message to figure out what it is

        :param post_limit: How far back should we go?
        :type post_limit: int
        """
        # This is for the progress bar
        iteration = 1
        # Start the progress bar before we make the request.
        line = CLI.get_progress_line(iteration, post_limit)
        self.screen.set_current_action_status("Checking Inbox for requests", line)
        for message in self.reddit.inbox.all(limit=post_limit):
            # If we're on the first one, show the progress bar not moving so we dont go over 100%
            if iteration is 1:
                iteration = iteration + 1
                line = CLI.get_progress_line(1, post_limit + 1)
                self.screen.set_current_action_status("Checking Inbox for requests", line)
            else:
                iteration = iteration + 1
                line = CLI.get_progress_line(iteration, post_limit + 1)
                self.screen.set_current_action_status("Checking Inbox for requests", line)
            # noinspection PyBroadException
            try:
                self.process_message(message)
            except Exception as ex:
                logging.info("Could not process %s with exception %s" % (message.id, ex))

    def check_subs_for_posts(self, post_limit=25):
        """
        Check the subs listed in the config and send them to process_message
        Additionally, make sure any special post requirements listed in the config for the auto-subs are satisfied

        :param post_limit: How far back in our posts should we go?
        :type post_limit: int
        """
        subs = self.config.get_automatic_processing_subs()

        # Caluclate the total amount of posts to be parsed
        totalits = len(subs) * post_limit
        iters = 0
        for sub in subs:
            subr = self.reddit.subreddit(sub)
            for post in subr.new(limit=post_limit):
                iters += 1
                line = CLI.get_progress_line(iters, totalits)
                self.screen.set_current_action_status("Checking Subs for posts", line)

                if self.database.submission_exists(post.id):
                    continue
                title = post.title

                has_triggers = self.config.configfile.has_option(sub, 'triggers')
                has_threshold = self.config.configfile.has_option(sub, 'threshold')

                if has_triggers:
                    triggers = str(self.config.configfile[sub]['triggers']).split('|')
                    if not any(t in title.lower() for t in triggers):
                        logging.debug('Title %s doesnt appear to contain any of %s, adding to parsed and skipping'
                                      % (title, self.config.configfile[sub]["triggers"]))
                        self.database.submission_insert(post.id, post.author.name, title, post.url)
                        continue
                else:
                    logging.debug('No triggers were defined for %s, not checking' % sub)

                if has_threshold:
                    threshold = int(self.config.configfile[sub]['threshold'])
                    if post.score < threshold:
                        logging.debug('Threshold not met, not adding to parsed, just ignoring')
                        continue
                    else:
                        logging.debug('Threshold met, posting and adding to parsed')
                else:
                    logging.debug('No threshold for %s, replying to everything :)' % sub)
                processed = self.process_submission(post, None, None)
                if processed is not None:
                    processed_url = processed[0]
                    processed_submission = processed[1]
                    processed_source_comment = processed[2]
                    # processed_custom_title_exists = processed[3]
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
        """
        Process given message (remove, feedback, mark good/bad bot as read)

        :param message: the inbox message, comment reply or username mention
        :type message: praw.models.Message, praw.models.Comment
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

            processed = self.process_submission(message.submission, message, title,
                                                dm=False, request_body=body, customargs=customargs)
            if processed is not None:
                processed_url = processed[0]
                processed_submission = processed[1]
                processed_source_comment = processed[2]
                # processed_custom_title_exists = processed[3]
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
        """
        Process Submission Using t2utils given the above args, and use the other
            provided function to reply

        :param customargs:
        :param submission: Submission object containing image to parse
        :type submission: praw.models.submission
        :param source_comment: Comment that invoked if any did, may be NoneType
        :type source_comment: praw.models.Comment, None
        :param title: Custom title if any
        :type title: String, None
        :param dm:
        :type dm:
        :param request_body:
        :type request_body:
        """

        url = self.process_image_submission(submission=submission, custom_title=title, customargs=customargs)

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

        custom_title_exists = True if title is not None else False
        return [url, submission, source_comment, custom_title_exists]

    def redirect_to_comment(self, source_comment, comment, submission):
        """
        Reply to `source_comment` with a link to `comment`

        :type source_comment: praw.models.Comment
        :param source_comment: Comment we're replying to
        :type comment: praw.models.Comment
        :param comment: Our comment that we're going to redirect them to
        :param submission: The submission that its under (needed to link to the comment)
        :type submission: praw.models.Submission
        """
        com_url = messages.comment_url.format(postid=submission.id, commentid=comment.id)
        reply = messages.already_responded_message.format(commentlink=com_url)

        try:
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
        """
        Process a submission that we know is a request for processing

        :param custom_title: Custom Title to parse
        :type custom_title: str
        :param submission: Submission that points to a URL we need to process
        :type submission: praw.models.Submission
        :param commenter: Commenter who requested the thing
        :type commenter: praw.models.Redditor
        :param customargs: List of custom arguments. Not implemented, but lets be ready for it once we figure out how
        :type customargs: list
        :return: URL the image has been uploaded to, None if it failed to upload
        :rtype: String, None
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
            return None

        # Make sure author account exists
        if submission.author is None:
            self.database.submission_insert(submission.id, "deletedPost", submission.title, submission.url)
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
            return None

        if url.endswith('.gif') or url.endswith('.gifv'):
            # Lets try this again.
            # noinspection PyBroadException
            try:
                return self.process_gif(submission)
            except Exception as ex:
                logging.warning("gif upload failed with %s" % ex)
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
                return None
        except Exception as error:
            print(error)
            print('Exception on image conversion lines.')
            return None
        # noinspection PyBroadException
        try:
            image = RedditImage(img)
        except Exception as error:
            print('Could not create RedditImage with %s' % error)
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
        """

        :param submission:
        :type submission: praw.models.Submission
        """

        # TODO: Fix framerate issues

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
            print(error)
            print('Exception on image conversion lines.')
            return None

        img = Image.open(BytesIO(response.content))
        frames = []

        # Process Gif

        # Loop over each frame in the animated image
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
        frames[0].save(path_gif, save_all=True, append_images=frames[1:])
        # ff = ffmpy.FFmpeg(inputs={path_gif: None},outputs={path_mp4: None})
        # ff.run()

        # noinspection PyBroadException
        try:
            url = self.upload_to_gfycat(path_gif).url
            remove(path_gif)
        except Exception as ex:
            logging.error('Gif Upload Failed with %s, Returning' % ex)
            remove(path_gif)
            return None
        # remove(path_mp4)
        return url

    @staticmethod
    def get_params_from_twitter(link):
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
        try:
            response = self.upload_to_imgur(path_png)
        except Exception as ex:
            # Likely too large
            logging.warning('png upload failed with %s, trying jpg' % ex)
            try:
                response = self.upload_to_imgur(path_jpg)
            except Exception as ex:
                logging.error('jpg upload failed with %s, returning' % ex)
                return None
        finally:
            remove(path_png)
            remove(path_jpg)
        return response.link

    def upload_to_imgur(self, local_image_url):
        """

        :param local_image_url: local url of the image. Usually temp.jpg or temp.png
        :type local_image_url: str
        :return: Uploaded image object
        :rtype: pyimgur.Image
        """
        self.screen.set_current_action_status("Uploading to Imgur...", "")
        self.screen.set_imgur_status("Uploading...")
        response = self.imgur.upload_image(local_image_url, title="Uploaded by /u/%s" % self.config.bot_username)
        self.screen.set_current_action_status("Complete", "")
        self.screen.set_imgur_status("Connected")
        return response

    def upload_to_gfycat(self, local_gif_url):
        generated_gfycat = self.gfycat.upload_file(local_gif_url)
        return generated_gfycat

    def reply_imgur_url(self, url, submission, source_comment, custom_title=None, upscaled=False, customargs=None):
        """
        Send the reply with PRAW to the user who requested, or the submission if its auto

        :param customargs:
        :type custom_title: object
        :param custom_title:
        :param upscaled: If the image is below 500x500px it gets upscaled,
        :type upscaled: bool
        :param url: Imgur Url the titled image was uploaded to
        :type url: str
        :param submission: Submission that the post was on. Reply if source_comment = False
        :type submission: praw.models.Submission
        :param source_comment: Comment that invoked bot if it exists
        :type source_comment: praw.models.Comment
        :returns: True on success, False on failure
        :rtype: bool
        """

        self.screen.set_current_action_status('Creating reply', "")
        if submission.subreddit in self.config.get_minimal_sub_list():
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
        elif submission.subreddit.display_name.lower() == "freefolk":
            # noinspection PyTypeChecker
            reply = messages.standard_reply_template.format(
                image_url=url,
                warntag="Custom titles/arguments are in beta" if customargs else "",
                custom="custom " if custom_title and len(custom_title) > 0 else "",
                nsfw="(NSFW)" if submission.over_18 else '',
                upscaled=' (image was upscaled)\n\n' if upscaled else '',
                submission_id=submission.id
            )
            reply = reply + " ^| ^sEnTiENt"
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
        try:
            if source_comment:
                source_comment.reply(reply)
            else:
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
    """RedditImage class

    :param image: the image
    :type image: PIL.Image.Image
    """
    margin = 10
    min_size = 500
    # font_file = 'seguiemj.ttf'
    font_file = 'roboto-emoji.ttf'
    font_scale_factor = 16
    # Regex to remove resolution tag styled as such: '[1000 x 1000]'
    regex_resolution = re.compile(r'\s?\[[0-9]+\s?[xX*×]\s?[0-9]+\]')

    def __init__(self, image):
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
        return self.title

    def _split_title(self, title):
        """Split title on [',', ';', '.'] into multiple lines

        :param title: the title to split
        :type title: str
        :returns: split title
        :rtype: list[str]
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
        """Wrap title

        :param title: the title to wrap
        :type title: str
        :returns: wrapped title
        :rtype: list
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
        """Add title to new whitespace on image

        :param author: Author of the submission
        :type author: str
        :param customargs: List of custom arguments to parse
        :type customargs: list
        :param text_color:
        :param bg_color:
        :param title: the title to add
        :type title: str
        :param boot: if True, split title on [',', ';', '.'], else wrap text
        :type boot: bool
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


class Configuration(object):

    def __init__(self, config_file):
        self._config = configparser.ConfigParser()
        self._config.read(config_file)
        self.configfile = self._config
        self.maintainer = self._config['Title2ImageBot']['maintainer']
        self.bot_username = self._config['RedditAuth']['username']

    def get_automatic_processing_subs(self):
        sections = self._config.sections()
        sections.remove("RedditAuth")
        sections.remove("GfyCatAuth")
        sections.remove("IgnoreList")
        sections.remove("MinimalList")
        sections.remove("ImgurAuth")
        sections.remove("Title2ImageBot")
        return sections

    def get_user_ignore_list(self):
        ignore_list = []
        for i in self._config.items("IgnoreList"):
            ignore_list.append(i[0])
        return ignore_list

    def get_minimal_sub_list(self):
        minimal_list = []
        for i in self._config.items("MinimalList"):
            minimal_list.append(i[0])
        return minimal_list

    def get_gfycat_client_config(self):
        client_id = self._config['GfyCatAuth']['publicKey']
        client_secret = self._config['GfyCatAuth']['privateKey']
        username = self._config['GfyCatAuth']['username']
        password = self._config['GfyCatAuth']['password']
        client = gfycat.GfyCatClient(client_id, client_secret, username, password)
        return client

    def auth_reddit_from_config(self):
        return (praw.Reddit(client_id=self._config['RedditAuth']['publicKey'],
                            client_secret=self._config['RedditAuth']['privateKey'],
                            username=self._config['RedditAuth']['username'],
                            password=self._config['RedditAuth']['password'],
                            user_agent=self._config['RedditAuth']['userAgent']))

    def get_imgur_client_config(self):
        return pyimgur.Imgur(self._config['ImgurAuth']['publicKey'])


class BotDatabase(object):

    def __init__(self, db_filename, interface):
        self.interface = interface
        self._sql_conn = sqlite3.connect(db_filename)
        self._sql = self._sql_conn.cursor()

    def cleanup(self):
        self._sql_conn.commit()
        self._sql_conn.close()

    def message_exists(self, message_id):
        """Check if message exists in messages table

        :param message_id: the message id to check
        :type message_id: str
        :returns: True if message was found, else False
        :rtype: bool
        """
        self.interface.set_data_status("Querying...")
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM messages WHERE id=?)', (message_id,))
        self.interface.set_data_status("Connected")
        if self._sql.fetchone()[0]:
            return True
        else:
            return False

    def submission_exists(self, message_id):
        self.interface.set_data_status("Querying...")
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM submissions WHERE id=?)', (message_id,))
        self.interface.set_data_status("Connected")
        if self._sql.fetchone()[0]:
            return True
        else:
            return False

    def message_parsed(self, message_id):
        self.interface.set_data_status("Querying...")
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM messages WHERE id=? AND parsed=1)', (message_id,))
        self.interface.set_data_status("Connected")
        if self._sql.fetchone()[0]:
            return True
        else:
            return False

    def message_insert(self, message_id, message_author, subject, body):
        self.interface.set_data_status("Querying...")
        """Insert message into messages table"""
        self._sql.execute('INSERT INTO messages (id, author, subject, body) VALUES (?, ?, ?, ?)',
                          (message_id, message_author, subject, body))
        self._sql_conn.commit()
        self.interface.set_data_status("Connected")

    def submission_select(self, submission_id):
        """Select all attributes of submission
        :param submission_id: the submission id
        :type submission_id: str
        :returns: query result, None if id not found
        :rtype: dict, NoneType
        """
        self.interface.set_data_status("Querying...")
        self._sql.execute('SELECT * FROM submissions WHERE id=?', (submission_id,))
        result = self._sql.fetchone()
        self.interface.set_data_status("Connected")
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
        self.interface.set_data_status("Querying...")
        """Insert submission into submissions table"""
        self._sql.execute('INSERT INTO submissions (id, author, title, url) VALUES (?, ?, ?, ?)',
                          (submission_id, submission_author, title, url))
        self._sql_conn.commit()
        self.interface.set_data_status("Connected")

    def submission_set_retry(self, submission_id, delete_message=False, message=None):
        """Set retry flag for given submission, delete message from db if desired
        :param submission_id: the submission id to set retry
        :type submission_id: str
        :param delete_message: if True, delete message from messages table
        :type delete_message: bool
        :param message: the message to delete
        :type message: praw.models.Comment, NoneType
        """
        self.interface.set_data_status("Querying...")
        self._sql.execute('UPDATE submissions SET retry=1 WHERE id=?', (submission_id,))
        if delete_message:
            if not message:
                raise TypeError('If delete_message is True, message must be set')
            self._sql.execute('DELETE FROM messages WHERE id=?', (message.id,))
        self._sql_conn.commit()
        self.interface.set_data_status("Connected")

    def submission_clear_retry(self, submission_id):
        """Clear retry flag for given submission_id
        :param submission_id: the submission id to clear retry
        :type submission_id: str
        """
        self.interface.set_data_status("Querying...")
        self._sql.execute('UPDATE submissions SET retry=0 WHERE id=?', (submission_id,))
        self._sql_conn.commit()
        self.interface.set_data_status("Connected")

    def submission_set_imgur_url(self, submission_id, imgur_url):
        """Set imgur url for given submission
        :param submission_id: the submission id to set imgur url
        :type submission_id: str
        :param imgur_url: the imgur url to update
        :type imgur_url: str
        """
        self.interface.set_data_status("Querying...")
        self._sql.execute('UPDATE submissions SET imgur_url=? WHERE id=?',
                          (imgur_url, submission_id))
        self._sql_conn.commit()
        self.interface.set_data_status("Connected")


class CLI(object):

    def __init__(self):
        """
        Initialize our command line interface.

        If you want to clean up everything related to this please do because I hate it
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
        self.loglvl = "Debug" if (logging.getLogger().level == logging.DEBUG) else "Standard"
        self.catx = self.cols - 36
        self.caty = 4

    def set_reddit_user(self, reddituser):
        self.reddituser = reddituser
        self.update_bot_status_info()

    def set_reddit_status(self, redditstatus):
        self.redditstatus = redditstatus
        self.update_bot_status_info()

    def set_imgur_status(self, imgurstatus):
        # TODO: Proper global imgur rate limit handling
        self.imgurstatus = imgurstatus
        self.update_bot_status_info()

    def set_data_status(self, datastatus):
        self.datastatus = datastatus
        self.update_bot_status_info()

    def update_bot_status_info(self):
        self.stdscr.refresh()
        self.clear_line(0)
        self.clear_line(5)
        self.clear_line(6)
        self.clear_line(7)
        self.clear_line(9)
        self.stdscr.addstr(0, 0, "Title2ImageBot Version %s by CalicoCatalyst" % __version__)
        self.stdscr.addstr(5, 0, "Reddit Username : %s" % self.reddituser)
        self.stdscr.addstr(6, 0, "Reddit Status   : %s" % self.redditstatus)
        self.stdscr.addstr(7, 0, "Imgur Status    : %s" % self.imgurstatus)
        self.stdscr.addstr(9, 0, "Database Status : %s" % self.datastatus)
        self.stdscr.addstr(10, 0, "Logging Mode    : %s" % self.loglvl)
        self.print_cat(self.catx, self.caty)
        self.stdscr.refresh()

    @staticmethod
    def get_progress_line(iteration, total, prefix='', suffix='', decimals=1, bar_length=25):
        """
        Call in a loop to create terminal progress bar
        @params:
            iteration   - Required  : current iteration (Int)
            total       - Required  : total iterations (Int)
            prefix      - Optional  : prefix string (Str)
            suffix      - Optional  : suffix string (Str)
            decimals    - Optional  : positive number of decimals in percent complete (Int)
            bar_length  - Optional  : character length of bar (Int)
        """
        str_format = "{0:." + str(decimals) + "f}"
        percents = str_format.format(100 * (iteration / float(total)))
        filled_length = int(round(bar_length * iteration / float(total)))
        bar = '+' * filled_length + '-' * (bar_length - filled_length)

        return '%s|%s| %s%s %s' % (prefix, bar, percents, '%', suffix)

    def set_current_action_status(self, action, statusline):
        """progress: 0-10"""
        self.clear_line(14)
        self.clear_line(15)
        self.stdscr.addstr(14, 0, "%s" % action)
        self.stdscr.addstr(15, 0, "%s" % statusline)
        self.print_cat(self.catx, self.caty)
        self.stdscr.refresh()

    def clear_line(self, y):
        self.stdscr.move(y, 0)
        self.stdscr.clrtoeol()
        self.print_cat(self.catx, self.caty)
        self.stdscr.refresh()

    def print_cat(self, startx, starty):
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
    # Parse CLI args
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

    # noinspection PyBroadException
    try:
        bot.imgur.get_image('S1jmapR')
        interface.set_imgur_status("Connected")
    except Exception as ex:
        interface.set_imgur_status("Unable to connect with %s" % ex)

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
        while True:
            bot.call_checks(args.limit)
            interface.set_current_action_status('Checking Complete', "")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Command line debugging enabled")
        # TODO: Clean this up w/ curse
        curses.echo()
        curses.nocbreak()
        curses.endwin()
        while True:
            try:
                command = raw_input(">>>    ")
            except KeyboardInterrupt:
                break
            if str(command) == "quit":
                break
            exec(command)
        logging.debug("KeyboardInterrupt Detected, Cleaning up and exiting...")
        print("Cleaning up and exiting...")
        database.cleanup()
        exit(0)
    except Exception:
        bot.reddit.redditor(bot.config.maintainer).message("bot crash", "Bot Crashed :p")
        curses.echo()
        curses.nocbreak()
        curses.endwin()


if __name__ == '__main__':
    main()
