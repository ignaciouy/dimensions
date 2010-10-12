#Copyright (c) 2009,10 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

import pygtk
pygtk.require('2.0')
import gtk

from sugar.activity import activity
try:
    from sugar.activity.widgets import ActivityToolbarButton
    from sugar.activity.widgets import StopButton
    from sugar.graphics.toolbarbox import ToolbarBox
    from sugar.graphics.toolbarbox import ToolbarButton
    """
    from namingalert import NamingAlert
    """
    _new_sugar_system = True
except ImportError:
    _new_sugar_system = False
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.objectchooser import ObjectChooser
from sugar.datastore import datastore
from sugar import mime

import telepathy
from dbus.service import signal
from dbus.gobject_service import ExportedGObject
from sugar.presence import presenceservice
from sugar.presence.tubeconn import TubeConnection

from gettext import gettext as _
import os.path
import logging
_logger = logging.getLogger('visualmatch-activity')
try:
    _old_sugar_system = False
    import json
    from json import load as jload
    from json import dump as jdump
except(ImportError, AttributeError):
    try:
        import simplejson as json
        from simplejson import load as jload
        from simplejson import dump as jdump
    except ImportError:
        _old_sugar_system = True

from StringIO import StringIO

from constants import DECKSIZE, PRODUCT, HASH, ROMAN, WORD, CHINESE, MAYAN, \
                      INCAN, DOTS, STAR, DICE, LINES, DEAL, HIGH, MEDIUM, LOW, \
                      DIFFICULTY_LEVEL

from game import Game

LEVEL_ICONS = ['level2', 'level3', 'level1']
LEVEL_LABELS = [_('intermediate'), _('expert'), _('beginner')]
LEVEL_DECKSIZE = [DECKSIZE / 3, DECKSIZE, DECKSIZE / 9]

SERVICE = 'org.sugarlabs.VisualMatchActivity'
IFACE = SERVICE
PATH = '/org/augarlabs/VisualMatchActivity'


def _button_factory(icon_name, tooltip, callback, toolbar, cb_arg=None,
                    accelerator=None):
    """Factory for making toolbar buttons"""
    my_button = ToolButton(icon_name)
    my_button.set_tooltip(tooltip)
    my_button.props.sensitive = True
    if accelerator is not None:
        my_button.props.accelerator = accelerator
    if cb_arg is not None:
        my_button.connect('clicked', callback, cb_arg)
    else:
        my_button.connect('clicked', callback)
    if hasattr(toolbar, 'insert'):  # the main toolbar
        toolbar.insert(my_button, -1)
    else:  # or a secondary toolbar
        toolbar.props.page.insert(my_button, -1)
    my_button.show()
    return my_button


def _label_factory(label, toolbar):
    """ Factory for adding a label to a toolbar """
    my_label = gtk.Label(label)
    my_label.set_line_wrap(True)
    my_label.show()
    _toolitem = gtk.ToolItem()
    _toolitem.add(my_label)
    toolbar.insert(_toolitem, -1)
    _toolitem.show()
    return my_label


def _spin_factory(default, min, max, callback, toolbar):
    """ Factory for adding a spinner """
    _spin_adj = gtk.Adjustment(default, min, max, 5, 15, 0)
    my_spin = gtk.SpinButton(_spin_adj, 0, 0)
    _spin_id = my_spin.connect('value-changed', callback)
    my_spin.set_numeric(True)
    my_spin.show()
    _toolitem = gtk.ToolItem()
    _toolitem.add(my_spin)
    toolbar.insert(_toolitem, -1)
    _toolitem.show()
    return my_spin


def _separator_factory(toolbar, visible=True, expand=False):
    """ Factory for adding a separator to a toolbar """
    _separator = gtk.SeparatorToolItem()
    _separator.props.draw = visible
    _separator.set_expand(expand)
    toolbar.insert(_separator, -1)
    _separator.show()


def _find_the_number_in_the_name(name):
    """ Find which element in an array (journal entry title) is a number """
    parts = name.split('.')
    before = ''
    after = ''
    for i in range(len(parts)):
        ii = len(parts) - i - 1
        try:
            int(parts[ii])
            for j in range(ii):
                before += (parts[j] + '.')
            for j in range(ii + 1, len(parts)):
                after += ('.' + parts[j])
            return before, after, ii
        except ValueError:
            pass
    return '', '', -1


def _construct_a_name(before, i, after):
    """ Make a numbered file name from parts """
    name = ''
    if before != '':
        name += before
    name += str(i)
    if after != '':
        name += after
    return name


class VisualMatchActivity(activity.Activity):
    """ Dimension matching game """
    def __init__(self, handle):
        """ Initialize the Sugar activity """
        super(VisualMatchActivity, self).__init__(handle)

        # Set things up.
        self._read_journal_data()
        datapath = self._find_datapath(_old_sugar_system)
        self._setup_toolbars(_new_sugar_system)
        canvas = self._setup_canvas(datapath)
        self._setup_presence_service()

        # Then start playing the game.
        if not hasattr(self, '_saved_state'):
            self._saved_state = None
        self.vmw.new_game(self._saved_state, self._deck_index)
        if self._editing_word_list:
            self.vmw.editing_word_list = True
            self.vmw.edit_word_list()

    def _select_game_cb(self, button, card_type):
        """ Choose which game we are playing. """
        if self.vmw.joiner():  # joiner cannot change level
            return
        self.vmw.card_type = card_type
        if card_type == 'custom' and len(self.vmw.custom_paths) < 9:
            self._import_cb()
        self.vmw.new_game()

    def _robot_cb(self, button):
        """ Toggle robot assist on/off """
        if self.vmw.robot:
            self.vmw.robot = False
            self.robot_button.set_tooltip(_('Play with the computer.'))
            self.robot_button.set_icon('robot-off')
        elif not self.vmw.editing_word_list:
            self.vmw.robot = True
            self.robot_button.set_tooltip(
                _('Stop playing with the computer.'))
            self.robot_button.set_icon('robot-on')

    def _level_cb(self, button):
        """ Cycle between levels """
        if self.vmw.joiner():  # joiner cannot change level
            return
        self.vmw.level += 1
        if self.vmw.level == len(LEVEL_LABELS):
            self.vmw.level = 0
        self.set_level_label()

    def set_level_label(self):
        self.level_label.set_text(self.calc_level_label(self.vmw.low_score,
                                                        self.vmw.level))
        self.level_button.set_icon(LEVEL_ICONS[self.vmw.level])
        self.vmw.new_game()

    def calc_level_label(self, low_score, play_level):
        """ Show the score. """
        if low_score[play_level] == -1:
            return LEVEL_LABELS[play_level]
        else:
            return "%s (%d:%02d)" % \
                    (LEVEL_LABELS[play_level],
                     int(low_score[play_level] / 60),
                     int(low_score[play_level] % 60))

    def _import_cb(self, button=None):
        """ Import custom cards from the Journal """
        chooser = None
        name = None
        try:
            # See #2398
            if 'image/svg+xml' not in \
                    mime.get_generic_type(mime.GENERIC_TYPE_IMAGE).mime_types:
                mime.get_generic_type(
                    mime.GENERIC_TYPE_IMAGE).mime_types.append('image/svg+xml')
            chooser = ObjectChooser(parent=self,
                                    what_filter=mime.GENERIC_TYPE_IMAGE)
        except TypeError:
            chooser = ObjectChooser(None, self,
                gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT)

        if chooser is not None:
            try:
                result = chooser.run()
                if result == gtk.RESPONSE_ACCEPT:
                    jobject = chooser.get_selected_object()
                    if jobject and jobject.file_path:
                        name = jobject.metadata['title']
                        mime_type = jobject.metadata['mime_type']
                        _logger.debug('result of choose: %s (%s)' % \
                                          (name, str(mime_type)))
            finally:
                chooser.destroy()
                del chooser

            if name is not None:
                self._find_custom_paths(name, mime_type)

    def _find_custom_paths(self, name, mime_type):
        basename, suffix, i = _find_the_number_in_the_name(name)
        if i < 0: # not part of a numbered sequence
            self.vmw.card_type = 'pattern'
            return
        dsobjects, nobjects = datastore.find({'mime_type': [str(mime_type)]})
        _logger.debug('%d' % nobjects)
        self.vmw.custom_paths = []
        if nobjects > 0:
            for j in range(DECKSIZE):
                for i in range(nobjects):
                    if dsobjects[i].metadata['title'] == \
                            _construct_a_name(basename, j + 1, suffix):
                        _logger.debug('result of find: %s' %\
                                          dsobjects[i].metadata['title'])
                        self.vmw.custom_paths.append(dsobjects[i].file_path)
                        break
        self.vmw.card_type = 'custom'
        if len(self.vmw.custom_paths) < 9:
            self.vmw.card_type = 'pattern'
        elif len(self.vmw.custom_paths) < 27:
            self.vmw.level = DIFFICULTY_LEVEL.index(LOW)
        elif len(self.vmw.custom_paths) < 81:
            self.vmw.level = DIFFICULTY_LEVEL.index(MEDIUM)
        else:
            self.vmw.level = DIFFICULTY_LEVEL.index(HIGH)
        if self.vmw.card_type == 'custom':
            self.button_custom.set_icon('new-custom-game')
            self.button_custom.set_tooltip(_('New custom game'))
            self.metadata['custom_name'] = name
            self.metadata['custom_mime_type'] = mime_type
        self.set_level_label()
        return

    def _number_card_O_cb(self, button, numberO):
        """ Choose between O-card list for numbers game. """
        if self.vmw.joiner():  # joiner cannot change decks
            return
        self.vmw.numberO = numberO
        self.vmw.card_type = 'number'
        self.vmw.new_game()

    def _number_card_C_cb(self, button, numberC):
        """ Choose between C-card list for numbers game. """
        if self.vmw.joiner():  # joiner cannot change decks
            return
        self.vmw.numberC = numberC
        self.vmw.card_type = 'number'
        self.vmw.new_game()

    def _robot_time_spin_cb(self, button):
        """ Set delay for robot. """
        self.vmw.robot_time = self._robot_time_spin.get_value_as_int()
        return

    def _edit_words_cb(self, button):
        """ Edit the word list. """
        self.vmw.editing_word_list = True
        self.vmw.edit_word_list()
        if self.vmw.robot:
            self._robot_cb(button)

    """
    def _write_to_journal_cb(self, button):
        title_alert = NamingAlert(self, activity.get_bundle_path())
        title_alert.set_transient_for(self.get_toplevel())
        title_alert.show()
        self.reveal()
        return True
    """

    def _read_journal_data(self):
        """ There may be data from a previous instance. """
        if 'play_level' in self.metadata:
            self._play_level = int(self.metadata['play_level'])
        else:
            self._play_level = 2
        if 'robot_time' in self.metadata:
            self._robot_time = int(self.metadata['robot_time'])
        else:
            self._robot_time = 60
        if 'cardtype' in self.metadata:
            self._card_type = self.metadata['cardtype']
        else:
            self._card_type = 'pattern'
        if 'numberO' in self.metadata:
            self._numberO = int(self.metadata['numberO'])
        else:
            self._numberO = PRODUCT
        if 'numberC' in self.metadata:
            self._numberC = int(self.metadata['numberC'])
        else:
            self._numberC = HASH
        if 'matches' in self.metadata:
            self._matches = int(self.metadata['matches'])
        else:
            self._matches = 0
        if 'robot_matches' in self.metadata:
            self._robot_matches = int(self.metadata['robot_matches'])
        else:
            self._robot_matches = 0
        if 'total_time' in self.metadata:
            self._total_time = int(self.metadata['total_time'])
        else:
            self._total_time = 0
        if 'deck_index' in self.metadata:
            self._deck_index = int(self.metadata['deck_index'])
        else:
            self._deck_index = 0
        if 'mouse' in self.metadata:
            self._word_lists = [[self.metadata['mouse'], \
                                 self.metadata['cat'], \
                                 self.metadata['dog']], \
                                [self.metadata['cheese'], \
                                 self.metadata['apple'], \
                                 self.metadata['bread']], \
                                [self.metadata['moon'], \
                                 self.metadata['sun'], \
                                 self.metadata['earth']]]
        else:
            self._word_lists = [[_('mouse'), _('cat'), _('dog')],
                                [_('cheese'), _('apple'), _('bread')],
                                [_('moon'), _('sun'), _('earth')]]
        if 'editing_word_list' in self.metadata:
            self._editing_word_list = bool(int(
                self.metadata['editing_word_list']))
        else:
            self._editing_word_list = False
        if 'low_score_intermediate' in self.metadata:
            self._low_score = [int(self.metadata['low_score_intermediate']),
                               int(self.metadata['low_score_expert']),
                               int(self.metadata['low_score_beginner'])]
        elif 'low_score_expert' in self.metadata:
            self._low_score = [-1,
                                int(self.metadata['low_score_expert']),
                                int(self.metadata['low_score_beginner'])]
        else:
            self._low_score = [-1, -1, -1]
        if self._card_type == 'custom':
            if 'custom_name' in self.metadata:
                self._basename = self.metadata['custom_name']
                self._mime_type = self.metadata['custom_mime_type']
            else:
                self._card_type = 'pattern'

    def _find_datapath(self, _old_sugar_system):
        """ Find the datapath for saving card files. """
        self._old_sugar_system = _old_sugar_system
        if self._old_sugar_system:
            return os.path.join(os.environ['HOME'], ".sugar", "default",
                                SERVICE, 'data')
        else:
            return os.path.join(self.get_activity_root(), 'data')

    def _setup_toolbars(self, new_sugar_system):
        """ Setup the toolbars.. """

        games_toolbar = gtk.Toolbar()
        tools_toolbar = gtk.Toolbar()
        numbers_toolbar = gtk.Toolbar()
        if new_sugar_system:
            toolbox = ToolbarBox()

            # Activity toolbar
            activity_button = ActivityToolbarButton(self)

            toolbox.toolbar.insert(activity_button, 0)
            activity_button.show()

            """
            # Naming alert button
            write_to_journal_button = _button_factory("journal-write",
                                                      _('Write in Journal'),
                                                      self._write_to_journal_cb,
                                                      toolbox.toolbar, None,
                                                      '<Ctrl>j')
            """

            games_toolbar_button = ToolbarButton(
                    page=games_toolbar,
                    icon_name='new-game')
            games_toolbar.show()
            toolbox.toolbar.insert(games_toolbar_button, -1)
            games_toolbar_button.show()

            numbers_toolbar_button = ToolbarButton(
                    page=numbers_toolbar,
                    icon_name='number-tools')
            numbers_toolbar.show()
            toolbox.toolbar.insert(numbers_toolbar_button, -1)
            numbers_toolbar_button.show()

            tools_toolbar_button = ToolbarButton(
                    page=tools_toolbar,
                    icon_name='view-source')
            tools_toolbar.show()
            toolbox.toolbar.insert(tools_toolbar_button, -1)
            tools_toolbar_button.show()

            self._set_labels(toolbox.toolbar)
            _separator_factory(toolbox.toolbar, False, True)

            stop_button = StopButton(self)
            stop_button.props.accelerator = '<Ctrl>q'
            toolbox.toolbar.insert(stop_button, -1)
            stop_button.show()

            self.set_toolbar_box(toolbox)
            toolbox.show()

            games_toolbar_button.set_expanded(True)
        else:
            # Use pre-0.86 toolbar design
            toolbox = activity.ActivityToolbox(self)
            self.set_toolbox(toolbox)
            toolbox.add_toolbar(_('Game'), games_toolbar)
            toolbox.add_toolbar(_('Numbers'), numbers_toolbar)
            toolbox.add_toolbar(_('Tools'), tools_toolbar)
            toolbox.show()
            toolbox.set_current_toolbar(1)

        # Add the buttons and spinners to the toolbars
        self.button_pattern = _button_factory('new-pattern-game',
                                       _('New pattern game'),
                                       self._select_game_cb, games_toolbar,
                                       'pattern')
        self.button_number = _button_factory('new-number-game',
                                       _('New number game'),
                                       self._select_game_cb, games_toolbar,
                                       'number')
        self.button_word = _button_factory('new-word-game',
                                       _('New word game'),
                                       self._select_game_cb, games_toolbar,
                                       'word')
        self.button_custom = _button_factory('insert-image',
                                       _('Import custom cards'),
                                       self._select_game_cb, games_toolbar,
                                       'custom')

        if new_sugar_system:
            self._set_extras(games_toolbar, games_toolbar=True)
        else:
            self._set_labels(games_toolbar)
            self._set_extras(tools_toolbar, games_toolbar=False)

        self.words_tool_button = _button_factory('word-tools',
                                                 _('Edit word lists.'),
                                                 self._edit_words_cb,
                                                 tools_toolbar)
        self.import_button = _button_factory('insert-image',
            _('Import custom cards'), self._import_cb, tools_toolbar)
        self.product_button = _button_factory('product', _('product'),
                                              self._number_card_O_cb,
                                              numbers_toolbar,
                                              PRODUCT)
        self.roman_button = _button_factory('roman', _('Roman numerals'),
                                            self._number_card_O_cb,
                                            numbers_toolbar,
                                            ROMAN)
        self.word_button = _button_factory('word', _('word'),
                                           self._number_card_O_cb,
                                           numbers_toolbar,
                                           WORD)
        self.chinese_button = _button_factory('chinese', _('Chinese'),
                                              self._number_card_O_cb,
                                              numbers_toolbar,
                                              CHINESE)
        self.mayan_button = _button_factory('mayan', _('Mayan'),
                                            self._number_card_O_cb,
                                            numbers_toolbar,
                                            MAYAN)
        self.quipu_button = _button_factory('incan', _('Quipu'),
                                            self._number_card_O_cb,
                                            numbers_toolbar,
                                            INCAN)
        _separator_factory(numbers_toolbar, True, False)
        self.hash_button = _button_factory('hash', _('hash marks'),
                                           self._number_card_C_cb,
                                           numbers_toolbar,
                                           HASH)
        self.dots_button = _button_factory('dots', _('dots in a circle'),
                                           self._number_card_C_cb,
                                           numbers_toolbar,
                                           DOTS)
        self.star_button = _button_factory('star', _('points on a star'),
                                           self._number_card_C_cb,
                                           numbers_toolbar,
                                           STAR)
        self.dice_button = _button_factory('dice', _('dice'),
                                           self._number_card_C_cb,
                                           numbers_toolbar,
                                           DICE)
        self.lines_button = _button_factory('lines', _('dots in a line'),
                                            self._number_card_C_cb,
                                            numbers_toolbar,
                                            LINES)

    def _set_extras(self, toolbar, games_toolbar=True):
        if games_toolbar:
            _separator_factory(toolbar, True, False)
        self.robot_button = _button_factory("robot-off",
                                            _('Play with the computer'),
                                            self._robot_cb, toolbar)
        self._robot_time_spin = _spin_factory(self._robot_time, 15, 180,
                                              self._robot_time_spin_cb,
                                              toolbar)
        _separator_factory(toolbar, True, False)
        self.level_button = _button_factory(LEVEL_ICONS[self._play_level],
                                            _('Set difficulty level.'),
                                            self._level_cb, toolbar)
        self.level_label = _label_factory(self.calc_level_label(
                self._low_score, self._play_level), toolbar)
        if not games_toolbar:
            _separator_factory(toolbar, True, False)

    def _set_labels(self, toolbar):
        """ Add labels to toolbar toolbar """
        self.status_label = _label_factory(_('Find a match.'), toolbar)
        _separator_factory(toolbar, True, False)
        self.deck_label = _label_factory("%d %s" % \
                                             (LEVEL_DECKSIZE[self._play_level]\
                                                  - DEAL, _('cards')), toolbar)
        _separator_factory(toolbar, True, False)
        self.match_label = _label_factory("%d %s" % (0, _('matches')), toolbar)
        _separator_factory(toolbar, True, False)
        self.clock_label = _label_factory("-", toolbar)

    def _setup_canvas(self, datapath):
        """ Create a canvas.. """
        canvas = gtk.DrawingArea()
        canvas.set_size_request(gtk.gdk.screen_width(),
                                gtk.gdk.screen_height())
        self.set_canvas(canvas)
        canvas.show()
        self.show_all()

        self.vmw = Game(canvas, datapath, self)
        self.vmw.level = self._play_level
        self.vmw.card_type = self._card_type
        self.vmw.robot = False
        self.vmw.robot_time = self._robot_time
        self.vmw.low_score = self._low_score
        self.vmw.numberO = self._numberO
        self.vmw.numberC = self._numberC
        self.vmw.matches = self._matches
        self.vmw.robot_matches = self._robot_matches
        self.vmw.total_time = self._total_time
        self.vmw.buddies = []
        self.vmw.word_lists = self._word_lists
        self.vmw.editing_word_list = self._editing_word_list
        if self.vmw.card_type == 'custom':
            self._find_custom_paths(self._basename, self._mime_type)
        return canvas

    def write_file(self, file_path):
        """ Write data to the Journal. """
        _logger.debug("Saving to: %s" % file_path)
        if hasattr(self, 'vmw'):
            self.metadata['play_level'] = self.vmw.level
            self.metadata['low_score_beginner'] = int(self.vmw.low_score[0])
            self.metadata['low_score_intermediate'] = int(self.vmw.low_score[1])
            self.metadata['low_score_expert'] = int(self.vmw.low_score[2])
            self.metadata['robot_time'] = self.vmw.robot_time
            self.metadata['numberO'] = self.vmw.numberO
            self.metadata['numberC'] = self.vmw.numberC
            self.metadata['cardtype'] = self.vmw.card_type
            self.metadata['matches'] = self.vmw.matches
            self.metadata['robot_matches'] = self.vmw.robot_matches
            self.metadata['total_time'] = int(self.vmw.total_time)
            self.metadata['deck_index'] = self.vmw.deck.index
            self.metadata['mouse'] = self.vmw.word_lists[0][0]
            self.metadata['cat'] = self.vmw.word_lists[0][1]
            self.metadata['dog'] = self.vmw.word_lists[0][2]
            self.metadata['cheese'] = self.vmw.word_lists[1][0]
            self.metadata['apple'] = self.vmw.word_lists[1][1]
            self.metadata['bread'] = self.vmw.word_lists[1][2]
            self.metadata['moon'] = self.vmw.word_lists[2][0]
            self.metadata['sun'] = self.vmw.word_lists[2][1]
            self.metadata['earth'] = self.vmw.word_lists[2][2]
            self.metadata['editing_word_list'] = self.vmw.editing_word_list
            self.metadata['mime_type'] = 'application/x-visualmatch'
            f = file(file_path, 'w')
            f.write(self._dump())
            f.close()
        else:
            _logger.debug("Deferring saving to %s" % file_path)

    def _dump(self):
        """ Dump game data to the journal."""
        data = []
        for i in self.vmw.grid.grid:
            if i is None or self.vmw.editing_word_list:
                data.append(None)
            else:
                data.append(i.index)
        for i in self.vmw.clicked:
            if i is None or self.vmw.editing_word_list:
                data.append(None)
            else:
                data.append(self.vmw.deck.spr_to_card(i).index)
        for i in self.vmw.deck.cards:
            if i is None or self.vmw.editing_word_list:
                data.append(None)
            else:
                data.append(i.index)
        for i in self.vmw.match_list:
            data.append(self.vmw.deck.spr_to_card(i).index)
        for i in self.vmw.word_lists:
            for j in i:
                data.append(j)

        if self._old_sugar_system:
            return json.write(data)
        else:
            io = StringIO()
            jdump(data, io)
            return io.getvalue()

    def read_file(self, file_path):
        """ Read data from the Journal. """
        _logger.debug("Resuming from: %s" % file_path)
        f = open(file_path, 'r')
        self._load(f.read())
        f.close()

    def _load(self, data):
        """ Load game data from the journal. """
        if self._old_sugar_system:
            saved_state = json.read(data)
        else:
            io = StringIO(data)
            saved_state = jload(io)
        if len(saved_state) > 0:
            self._saved_state = saved_state

    def _setup_presence_service(self):
        """ Setup the Presence Service. """
        self.pservice = presenceservice.get_instance()
        self.initiating = None  # sharing (True) or joining (False)

        # Add my buddy object to the list
        owner = self.pservice.get_owner()
        self.owner = owner
        self.vmw.buddies.append(self.owner)
        self._share = ""
        self.connect('shared', self._shared_cb)
        self.connect('joined', self._joined_cb)

    def _shared_cb(self, activity):
        """ Either set up initial share..."""
        if self._shared_activity is None:
            _logger.error("Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()")
            return

        self.initiating = True
        self.waiting_for_deck = False
        _logger.debug('I am sharing...')

        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan

        # call back for "NewTube" signal
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal\
            ('NewTube', self._new_tube_cb)

        _logger.debug('This is my activity: making a tube...')
        id = self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].OfferDBusTube(
            SERVICE, {})

    def _joined_cb(self, activity):
        """ ...or join an exisiting share. """
        if self._shared_activity is None:
            _logger.error("Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()")
            return

        self.initiating = False
        _logger.debug('I joined a shared activity.')

        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan

        # call back for "NewTube" signal
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal(\
            'NewTube', self._new_tube_cb)

        _logger.debug('I am joining an activity: waiting for a tube...')
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].ListTubes(
            reply_handler=self._list_tubes_reply_cb,
            error_handler=self._list_tubes_error_cb)

        self.waiting_for_deck = True

    def _list_tubes_reply_cb(self, tubes):
        """ Reply to a list request. """
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        """ Log errors. """
        _logger.error('ListTubes() failed: %s', e)

    def _new_tube_cb(self, id, initiator, type, service, params, state):
        """ Create a new tube. """
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s '
                     'params=%r state=%d', id, initiator, type, service,
                     params, state)

        if (type == telepathy.TUBE_TYPE_DBUS and service == SERVICE):
            if state == telepathy.TUBE_STATE_LOCAL_PENDING:
                self.tubes_chan[ \
                              telepathy.CHANNEL_TYPE_TUBES].AcceptDBusTube(id)

            tube_conn = TubeConnection(self.conn,
                self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES], id, \
                group_iface=self.text_chan[telepathy.CHANNEL_INTERFACE_GROUP])

            # We'll use a chattube to send serialized data back and forth.
            self.chattube = ChatTube(tube_conn, self.initiating, \
                self.event_received_cb)

            # Now that we have the tube, we can ask for the deck of cards.
            if self.waiting_for_deck:
                self._send_event("j")

    def event_received_cb(self, text):
        """ Data is passed as tuples: cmd:text """
        if text[0] == 'B':
            e, card_index = text.split(':')
            _logger.debug("receiving card index: " + card_index)
            self.vmw._process_selection(
                self.vmw.deck.index_to_card(int(card_index)).spr)
        elif text[0] == 'S':
            e, card_index = text.split(':')
            _logger.debug("receiving selection index: " + card_index)
            self.vmw._process_selection(
                self.vmw.selected[int(card_index)].spr)
        elif text[0] == 'j':
            if self.initiating:  # Only the sharer "shares".
                _logger.debug("serialize the project and send to joiner")
                self._send_event("P:" + str(self.vmw.level))
                self._send_event("X:" + str(self.vmw.deck.index))
                self._send_event("M:" + str(self.vmw.matches))
                self._send_event("C:" + self.vmw.card_type)
                self._send_event("D:" + str(self._dump()))
        elif text[0] == 'J':  # Force a request for current state.
            self._send_event("j")
            self.waiting_for_deck = True
        elif text[0] == 'C':
            e, text = text.split(':')
            _logger.debug("receiving card_type from sharer " + text)
            self.vmw.card_type = text
        elif text[0] == 'P':
            e, text = text.split(':')
            _logger.debug("receiving play level from sharer " + text)
            self.vmw.level = int(text)
            self.level_label.set_text(self.calc_level_label(
                self.vmw.low_score, self.vmw.level))
            self.level_button.set_icon(LEVEL_ICONS[self.vmw.level])
        elif text[0] == 'X':
            e, text = text.split(':')
            _logger.debug("receiving deck index from sharer " + text)
            self.vmw.deck.index = int(text)
        elif text[0] == 'M':
            e, text = text.split(':')
            _logger.debug("receiving matches from sharer " + text)
            self.vmw.matches = int(text)
        elif text[0] == 'D':
            if self.waiting_for_deck:
                e, text = text.split(':')
                _logger.debug("receiving deck data from sharer")
                self._load(text)
                self.waiting_for_deck = False
            self.vmw.new_game(self._saved_state, self.vmw.deck.index)

    def _send_event(self, entry):
        """ Send event through the tube. """
        if hasattr(self, 'chattube') and self.chattube is not None:
            self.chattube.SendText(entry)


class ChatTube(ExportedGObject):
    """ Class for setting up tube for sharing """
    def __init__(self, tube, is_initiator, stack_received_cb):
        super(ChatTube, self).__init__(tube, PATH)
        self.tube = tube
        self.is_initiator = is_initiator  # Are we sharing or joining activity?
        self.stack_received_cb = stack_received_cb
        self.stack = ''

        self.tube.add_signal_receiver(self.send_stack_cb, 'SendText', IFACE,
                                      path=PATH, sender_keyword='sender')

    def send_stack_cb(self, text, sender=None):
        if sender == self.tube.get_unique_name():
            return
        # _logger.debug("This connection has no unique name yet.")
        self.stack = text
        self.stack_received_cb(text)

    @signal(dbus_interface=IFACE, signature='s')
    def SendText(self, text):
        self.stack = text
