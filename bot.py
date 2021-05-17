#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Title2ImageBot
Complete redesign of titletoimagebot with non-deprecated apis

Always striving to improve this bot, fix bugs that crash it, and keep pushing forward with its design.
Contributions welcome

Written and maintained by CalicoCatalyst

"""
import logging
import re
import sys
from io import BytesIO
from math import ceil

import requests
from PIL import Image, ImageSequence, ImageFont, ImageDraw
import csv

__author__ = 'calicocatalyst'
# [Major, e.g. a complete source code refactor].[Minor e.g. a large amount of changes].[Feature].[Patch]
__version__ = '1.1.3.0'


class TitleToImageManager(object):
    """Class for the parser itself."""

    def parse_image(self, url, title, date, customargs):

        if url.lower().endswith('.gif') or url.lower().endswith('.gifv'):
            # Lets try this again.
            # noinspection PyBroadException
            try:

                #######################
                #    PROCESS GIFS     #
                #######################
                return self.process_gif(url, title, customargs)
            except Exception as ex:
                logging.warning("gif save failed with %s" % ex)
                #######################
                #         BAIL        #
                #######################
                return None

        # Attempt to grab the images
        try:
            img = Image.open("img/" + url)
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
            image = CaptionedImage(img)
        except Exception as error:
            logging.error('Could not create CaptionImage with %s' % error)
            #######################
            #         BAIL        #
            #######################
            return None

        # I absolutely hate this method, would much rather just test length but people on StackOverflow bitch so

        image.add_title(title=title, date=date, customargs=customargs)

        image.save("out/" + url)

        return "out/" + url

    def process_gif(self, url, title, customargs):
        """Process a gif.

        Notes:
            This is ineffecient and awful. I need to either research and get familiar with animated picture processing
            in python or call up on the expertise of someone experienced in the area; Also considering building a
            library to do so in a better/more suitable language and calling it as a subprocess, which would work great
            as well

            See my GfyPy project for information on how that library works.

        Args:
            url: path to image
            title: Caption for image

        """

        img = Image.open("img/"+url)
        duration_list = self.find_duration(img)
        frames = []

        # Process Gif
        # We do this by creating a reddit image for every frame of the gif
        # This is godawful, but the impact on performance isn't too bad

        # Loop over each frame in the animated image
        for frame in ImageSequence.Iterator(img):
            # Draw the text on the frame

            # We'll create a custom CaptionImage for each frame to avoid
            #      redundant code

            r_frame = CaptionedImage(frame)
            r_frame.add_title(title, customargs)

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
        path_gif = 'out/'+url
        # path_mp4 = 'temp.mp4'
        frames[0].save(path_gif, save_all=True, append_images=frames[1:], duration=duration_list)
        return path_gif

    def find_duration(self, img_obj):
        duration_list = list()
        img_obj.seek(0)  # move to the start of the gif, frame 0
        # run a while loop to loop through the frames
        while True:
            try:
                frame_duration = img_obj.info['duration']  # returns current frame duration in milli sec.
                duration_list.append(frame_duration)
                # now move to the next frame of the gif
                img_obj.seek(img_obj.tell() + 1)  # image.tell() = current frame
            except EOFError:
                return duration_list


class CaptionedImage:
    """Reddit Image class

    A majority of this class is the work of gerenook, the author of the original bot. Its ingenious work, and
    the bot absolutely could not function without it. Anything dumb here is my (CalicoCatalyst) work.
    custom arguments are my work.
    I'm going to do my best to document it.

    Attributes:
        image (Image): PIL.Image object. Once methods are ran, will contain the output as well.
        upscale (bool): Was the image upscaled?
        title (str): Title we add to the image
    """

    margin = 10
    min_size = 500
    # font_file = 'seguiemj.ttf'
    font_file = 'Newsreader-Light.ttf'
    font_scale_factor = 32
    # Regex to remove resolution tag styled as such: '[1000 x 1000]'
    regex_resolution = re.compile(r'\s?\[[0-9]+\s?[xX*×]\s?[0-9]+\]')

    def __init__(self, image):
        """Create an image object, and pass an image file to it. The CaptionImage object then allows us to modify it.


        Args:
            image (Optional[any]): Image to be processed
        """

        self.image = image
        self.upscale = False
        self.title = ""
        self.date = ""

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
            self.upscale = True
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
            if self._font_title.getsize(lines[-1])[0] + CaptionedImage.margin > self._width:
                lines[-1] = lines[-1][:-len(word)].strip()
                lines.append(word)
                line_words = [word]
        # remove empty lines
        return [line for line in lines if line]

    def add_title(self, title, customargs, date=None, bg_color='#fff', text_color='#000'):
        """Add the title to an image

        return function is not used.

        Args:
            title (str): The title to add
            customargs (str): Custom arguments passed to the image parser
            date (str): Optional date to add to the title
            bg_color (str): Background of the title section
            text_color (str): Foreground (text) color

        Returns:
            Edited image
        """
        self.title = title

        title_centering = False
        dark_mode = False

        if customargs is not None:
            for arg in customargs:
                if arg == "center":
                    title_centering = True
                if arg == "dark":
                    dark_mode = True

        if dark_mode:
            bg_color = '#000'
            text_color = '#fff'

        # remove resolution appended to title (e.g. '<title> [1000 x 1000]')

        title = CaptionedImage.regex_resolution.sub('', title)
        if date:
            date = CaptionedImage.regex_resolution.sub('', date)
            title = date + " - " + title
        line_height = self._font_title.getsize(title)[1] + CaptionedImage.margin
        lines = self._wrap_title(title)
        whitespace_height = (line_height * len(lines)) + CaptionedImage.margin
        new = Image.new('RGB', (self._width, self._height + whitespace_height), bg_color)
        new.paste(self.image, (0, 0))
        draw = ImageDraw.Draw(new)
        for i, line in enumerate(lines):
            w, h = self._font_title.getsize(line)
            left_margin = ((self._width - w) / 2) if title_centering else CaptionedImage.margin
            draw.text((left_margin, i * line_height + CaptionedImage.margin + self.image.height),
                      line, text_color, self._font_title)

        self._width, self._height = new.size
        self.image = new
        return self.image

    def save(self, url):
        self.image.save(url)


def parse_custom_args(row):
    custom_args = []
    if string_to_bool(row[3]):
        custom_args.append('dark')
    if string_to_bool(row[4]):
        custom_args.append('center')
    return custom_args


def string_to_bool(v):
    return v.lower() in ("yes", "y", "true", "t", "1")


def parse_csv(data):
    reader = csv.reader(data)
    reader.__next__()  # Skip the first line - data
    manager = TitleToImageManager()
    try:
        for row in reader:
            url = row[0]
            date = row[1]
            title = row[2]
            customargs = parse_custom_args(row)
            manager.parse_image(url, title, date, customargs)
            print(row)
    except csv.Error as e:
        sys.exit('file {}, line {}: {}'.format(data.name, reader.line_num, e))


def main():
    filename = 'data.csv'
    with open(filename) as data:
        parse_csv(data)


if __name__ == '__main__':
    main()
