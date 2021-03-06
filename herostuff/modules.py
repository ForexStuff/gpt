#!/usr/bin/env python
# -*- coding: utf-8 -*-

__version__ = "0.5"

import codecs
import errno
import fileinput
import getpass
import gettext
import glob
import locale
import logging
import logging.config
import os
import shutil
import subprocess
import sys
import threading
import time

import yaml
from lxml import etree
import setproctitle

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gtk, Gdk, Gst, GLib, Gio
except (ImportError, ValueError) as e:
    print("Could not load GObject Python bindings.")
    print(e)
    sys.exit(1)

_ = gettext.gettext


class SliderUpdateException(Exception):
    pass


class Handler:
    """Signal assignment for Glade"""

    # ########## close/destroy  window ############

    def on_window_close(self, widget, *event):
        widget.hide_on_delete()
        return True

    def on_window_destroy(self, widget):
        app.on_app_shutdown(app.app)

    # ########## popover menu #####################

    def on_appwin_preview_clicked(self, widget):
        self.on_window_close(app.window)
        app.load_player_window()
        app.on_app_activate(app.app)

    def on_appwin_normal_clicked(self, widget):
        self.on_window_close(app.window)
        app.load_application_window()
        app.on_app_activate(app.app)

    def on_kd_support_stateset(self, widget, state):
        cli.kd_supp = state
        cli.change_kd_support_config(cli.kd_supp)

    def on_menu_about_activate(self, widget):
        app.obj("aboutdialog").run()

    def on_tl_calc_activate(self, widget):
        app.obj("tl_calc_win").show_all()

    def on_radio_compact_toggled(self, widget):
        if widget.get_active():
            cli.change_appview_config("compact")
        else:
            cli.change_appview_config("ext")

    # ########## toolbar ##########################

    # left toolbar (working directory)
    def on_changewdir_clicked(self, widget):
        win = FileChooserDialog()
        win.on_folder_clicked()
        cli.chkdir(win.selectedfolder)
        cli.stdir = os.getcwd()
        app.show_workdir()
        app.load_dircontent()
        cli.replace_wdir_config(win.selectedfolder)

    def on_refresh_wdir_clicked(self, widget):
        app.load_dircontent()
        app.discspace_info()

    def on_open_wdir_clicked(self, widget):
        subprocess.run(["xdg-open", cli.stdir])

    # right toolbar (memory card)
    def on_import_sd_clicked(self, widget):
        app.get_targetfolderwindow_content()
        self.on_find_sd_clicked(None)

    def on_find_sd_clicked(self, widget):
        # delete sd content info and no space info
        app.obj("sd_content_info").set_text("")
        app.obj("nospace_info").set_text("")
        app.find_sd()
        app.discspace_info()

    def on_open_sd_clicked(self, widget):
        subprocess.run(["xdg-open", cli.cardpath])

    def on_format_sd_clicked(self, widget):
        app.obj("confirm_format_dialog").show_all()
        self.on_find_sd_clicked(None)

    def on_import_other_clicked(self, widget):
        app.get_targetfolderwindow_content()
        app.obj("nospace_info").set_text("")
        app.discspace_info()

    def on_choose_other_location_clicked(self, widget):
        win = FileChooserDialog()
        win.on_folder_clicked()
        app.obj("act_othloc").set_text(win.selectedfolder)
        app.obj("dir_content_info").set_text(cli.card_content(win.selectedfolder))
        if cli.abs_size == 0:
            app.obj("import_other").set_sensitive(False)
            cli.show_message(_("No files here to import..."))
        elif cli.freespace(win.selectedfolder, cli.stdir):
            app.obj("import_other").set_sensitive(True)
            cli.cardpath = win.selectedfolder
        else:
            app.obj("import_other").set_sensitive(False)
            app.obj("nospace_info").set_text(
                _("Not enough disc space.\nFree at least {}.").format(cli.needspace))

    # treeview table
    def on_treeview_selection_changed(self, widget):
        row, pos = widget.get_selected()
        if pos:
            # absolute path stored in 5th column in treestore, not displayed in treeview
            self.sel_folder = row[pos][4]
            app.sel_folder = self.sel_folder
            # n umber of video files
            self.sel_vid = row[pos][1]
            app.activate_tl_buttons(row[pos][1], row[pos][2], row[pos][4], row[pos][6])

    def on_cellrenderertext_edited(self, widget, pos, edit):
        # new folder is split(head)+edit
        newdir = os.path.join(os.path.split(self.sel_folder)[0], edit)
        counter = 0
        # only do something if cell was actually edited
        if edit != os.path.split(self.sel_folder)[1]:
            while True:
                # check if directory exists
                if os.path.isdir(newdir) is False:
                    try:
                        os.replace(self.sel_folder, newdir)
                        cli.show_message(_("Folder renamed"))
                        app.get_window_content()
                        break
                    except OSError:
                        cli.log.exception(_("Exception error"))
                        raise
                else:
                    # if directory already exists just add a number at the end
                    counter += 1
                    if counter > 1:
                        newdir = newdir[:-len(str(counter))]
                    newdir = "{}{}".format(newdir, counter)
                    cli.log.warning(_("Directory already exists. Trying {}...").format(newdir))
        else:
            cli.show_message(_("New name is old name, there is nothing to do here."))

    # calculate timelapse
    def on_tlvideo_button_clicked(self, widget):
        app.obj("multwindow").run()

    def on_tlimage_button_clicked(self, widget):
        app.timelapse_img(self.sel_folder)

    def on_tlimage_sub_button_clicked(self, widget):
        app.timelapse_img_subfolder(self.sel_folder)

    # right click menu in treeview
    def on_treeview_button_release_event(self, widget, event):
        try:
            # define context menu
            popup = Gtk.Menu()
            kd_item = Gtk.MenuItem(_("Open with Kdenlive"))
            # selected row is already caught by on_treeview_selection_changed function
            kd_item.connect("activate", self.on_open_with_kdenlive, self.sel_folder)

            # don"t show menu item if there are no video files
            if self.sel_vid > 0 and cli.kd_supp is True:
                popup.append(kd_item)

            open_item = Gtk.MenuItem(_("Open folder"))
            open_item.connect("activate", self.on_open_folder, self.sel_folder)
            popup.append(open_item)
            popup.show_all()
            # only show on right click
            if event.button == 3:
                popup.popup(None, None, None, None, event.button, event.time)
                return True
        except AttributeError:
            # this error (missing variable self.sel_folder) is returned when clicking on title row
            # ignoring because there is nothing to happen on right click
            pass

    def on_open_with_kdenlive(self, widget, folder):
        kds.create_project(folder)

    def on_open_folder(self, widget, folder):
        subprocess.run(["xdg-open", folder])

    # #### set multiplier dialog #####

    def on_mult_response(self, widget, response):
        if response == -5:
            mult = app.obj("mult_spinbutton").get_value()
            self.on_window_close(widget)
            app.timelapse_vid(app.sel_folder, mult)
        else:
            self.on_window_close(widget)

    # #### select destination folder window ####

    def on_targetfolder_response(self, widget, response):
        if response == -5:
            self.on_window_close(widget)
            app.obj("importmessage").show_all()
            time.sleep(.1)
            cli.subpath_card = ""
            cli.copycard(cli.cardpath, os.path.join(cli.stdir, self.copyfolder))
            self.on_window_close(app.obj("importmessage"))
            app.load_dircontent()
            app.discspace_info()
        else:
            self.on_window_close(widget)

    def on_combobox1_changed(self, widget):
        row = widget.get_active_iter()
        if row:
            model = widget.get_model()
            self.copyfolder = model[row][0]
            cli.show_message(_("Selected: {}").format(self.copyfolder))
        else:
            self.copyfolder = widget.get_child().get_text()
            cli.show_message(_("Entered: {}").format(self.copyfolder))

    # #### Timelapse calculator window #####

    def on_spin_hours_value_changed(self, widget):
        tlc.dur_hours = tlc.get_spinbutton_data(widget)
        tlc.set_fileinfo()

    def on_spin_minutes_value_changed(self, widget):
        tlc.dur_min = tlc.get_spinbutton_data(widget)
        tlc.set_fileinfo()

    def on_spin_fps_value_changed(self, widget):
        tlc.fps = tlc.get_spinbutton_data(widget)
        tlc.set_fileinfo()

    def on_combobox_res_changed(self, widget):
        tlc.fsize = tlc.get_combobox_data(widget, 2)
        tlc.set_fileinfo()

    def on_combobox_intvl_changed(self, widget):
        tlc.intvl = tlc.get_combobox_data(widget, 1)
        tlc.set_fileinfo()

    # #### Confirm formatting SD card #####

    def on_confirm_format_dialog_response(self, widget, event):
        widget.hide_on_delete()
        if event == -5:
            cli.format_sd()
            app.find_sd()
            app.discspace_info()

    # #### Extended application window with media player included #####

    # treeview table in player window
    def on_treeview_selection_changed_pl(self, widget):
        row, pos = widget.get_selected()
        if pos:
            # absolute path stored in 5th column in treestore, not displayed in treeview
            self.sel_folder = row[pos][4]
            app.sel_folder = self.sel_folder
            # number of video files
            self.sel_vid = row[pos][1]
            app.activate_tl_buttons(row[pos][1], row[pos][2], row[pos][4], row[pos][6])

            # show folder content in 2nd treeview with liststore2 data
            app.obj("liststore2").clear()
            counter = 0

            try:
                for dirs in sorted(os.listdir(row[pos][4])):
                    if dirs.endswith("JPG") or dirs.endswith("MP4"):
                        counter += 1
                        path = os.path.join(self.sel_folder, dirs)
                        # transmit row to liststore
                        app.obj("liststore2").append([counter, dirs, path])
            except FileNotFoundError:
                pass  # this happens after renaming folders within the application

    def on_treeview_selection2_changed(self, widget):
        try:
            row, pos = widget.get_selected()
            self.playbackfile = row[pos][2]
        except:
            # n othing selected, stop playback
            ply.clear_playbin()

    # select file in treeview2 by clicking
    def on_treeview2_button_release_event(self, widget, event):
        ply.clear_playbin()

        # show mediainfo
        ply.mediainfo(self.playbackfile)

        ply.setup_player(self.playbackfile)
        if ply.playpause_button.get_active() is True:
            ply.playpause_button.set_active(False)
        else:
            ply.play()

        # set controls inactive if image file is shown in preview
        if self.playbackfile.endswith("JPG"):
            app.obj("media_control").set_sensitive(False)
        else:
            app.obj("media_control").set_sensitive(True)

    # #### media control buttons #####
    def on_playpause_togglebutton_toggled(self, widget):
        if ply.playpause_button.get_active():
            img = Gtk.Image.new_from_stock(Gtk.STOCK_MEDIA_PLAY, Gtk.IconSize.BUTTON)
            widget.set_property("image", img)
            ply.pause()
        else:
            img = Gtk.Image.new_from_stock(Gtk.STOCK_MEDIA_PAUSE, Gtk.IconSize.BUTTON)
            widget.set_property("image", img)
            ply.play()

    def on_forward_clicked(self, widget):
        ply.skip_minute()

    def on_backward_clicked(self, widget):
        ply.skip_minute(-1)

    def on_progress_value_changed(self, widget):
        ply.on_slider_seek

    # ####### stackswitchwer ############
    def on_stack_visible_child_name_notify(self, widget, param):
        # move treeview that lists the folder content to the visible stack child to avoid
        # duplicate code
        view = app.obj("stack").get_visible_child_name()
        if view == "ext":
            app.obj("content_wdir_compact").remove(app.obj("treeview_wdir"))
            app.obj("content_wdir_ext").add(app.obj("treeview_wdir"))
        else:
            app.obj("content_wdir_ext").remove(app.obj("treeview_wdir"))
            app.obj("content_wdir_compact").add(app.obj("treeview_wdir"))


class FileChooserDialog(Gtk.Window):
    """File chooser dialog when changing working directory"""
    # coder was too stupid to create a functional fcd with Glade so she borrowed some code
    # from the documentation site
    def on_folder_clicked(self):
        Gtk.Window.__init__(self, title=_("Change working directory"))
        dialog = Gtk.FileChooserDialog(_("Choose directory"),
                                       self,
                                       Gtk.FileChooserAction.SELECT_FOLDER,
                                       (Gtk.STOCK_CANCEL,
                                        Gtk.ResponseType.CANCEL,
                                        _("Apply"),
                                        Gtk.ResponseType.OK,
                                        )
                                       )
        dialog.set_default_size(800, 400)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.selectedfolder = dialog.get_filename()
        elif response == Gtk.ResponseType.CANCEL:
            self.selectedfolder = cli.stdir

        dialog.destroy()


class GoProGUI:

    def __init__(self):

        # Glade files/window configuration
        self.gladefiles = {"timelapse_calculator": "tlcalculator.glade",
                           "sub_windows": "gopro.glade",
                           "main_window": "appwindow.glade",
                           "main_window_mediaplayer": "playerwindow.glade",
                           "stack_window": "stack_window.glade"
                           }

        for f in self.gladefiles:
            self.gladefiles[f] = os.path.join(cli.install_dir,
                                              "ui",
                                              self.gladefiles[f])

        # set up builder
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain(cli.appname)
        self.obj = self.builder.get_object

        # load tlcalculator and subordinated window glade files
        self.builder.add_from_file(self.gladefiles["timelapse_calculator"])
        self.builder.add_from_file(self.gladefiles["sub_windows"])

        # setup Gtk application
        self.app = Gtk.Application.new(None, Gio.ApplicationFlags(0))

        # define commandline options to pass
        self.app.add_main_option_entries([
            self.create_option_entry("--version",
                                     "-v",
                                     description="Show version info"),
            self.create_option_entry("--default",
                                     description="Default GUI with integrated view switch"),
            self.create_option_entry("--alt-gui-compact",
                                     "-c",
                                     description="Alternative GUI, compact view"),
            self.create_option_entry("--alt-gui-ext",
                                     "-e",
                                     description="Alternative GUI, extended view (GStreamer preview)"),
            self.create_option_entry("--cli",
                                     description="Commandline interface"),
            self.create_option_entry("--tl-calc",
                                     "-t",
                                     description="Run the timelapse calculator")
        ])

        # connect basic application signals
        self.app.connect("startup", self.on_app_startup)
        self.app.connect("activate", self.on_app_activate)
        self.app.connect("shutdown", self.on_app_shutdown)
        self.app.connect("handle-local-options", self.on_local_option)

    def create_option_entry(self,
                            long_name,
                            short_name=None,
                            flags=0,
                            arg=GLib.OptionArg.NONE,
                            arg_data=None,
                            description=None,
                            arg_description=None
                            ):
        option = GLib.OptionEntry()
        option.long_name = long_name.lstrip("-")
        option.short_name = 0 if not short_name else ord(short_name.lstrip("-"))
        option.flags = flags
        option.arg = arg
        option.arg_data = arg_data
        option.description = description
        option.arg_description = arg_description
        return option

    def on_local_option(self, app, option):
        self.calc_sa = False
        if option.contains("version"):
            print("GPT:    {}".format(__version__))
            print("Python: {}".format(sys.version[:5]))
            print("GTK+:   {}.{}.{}".format(Gtk.MAJOR_VERSION,
                                            Gtk.MINOR_VERSION,
                                            Gtk.MICRO_VERSION,
                                            ))
            print(_("Application executed from {}".format(cli.install_dir)))
            return 0    # quit
        elif option.contains("cli"):
            cli.help()
            cli.shell()
            return 0
        elif option.contains("default"):
            self.load_stack_application_window()
        elif option.contains("alt-gui-compact"):
            self.load_application_window()
        elif option.contains("alt-gui-ext"):
            self.load_player_window()
        elif option.contains("tl-calc"):
            tlc.standalone()
            return 0
        else:
            # insert key do option GLibVariantDict
            option.insert_value("default", GLib.Variant("u", True))
            self.on_local_option(app, option)

        # contiunue with loading the app
        return -1

    def on_app_activate(self, app):
        self.builder.connect_signals(Handler())
        self.get_window_content()
        self.window.set_application(app)
        self.set_dialog_relations(self.window, self.obj)
        self.window.show_all()

    def on_app_shutdown(self, app):
        self.app.quit()
        cli.log.info(_("Application terminated on window close button. Bye."))

    def on_app_startup(self, app):
        # initiate custom css
        # css stylesheet
        stylesheet = os.path.join(cli.install_dir, "ui", "gtk.css")
        # ...encode() is needed because CssProvider expects byte type input
        with open(stylesheet, "r") as f:
            css = f.read().encode()

        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(css)

        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def load_stack_application_window(self):
        self.builder.add_from_file(self.gladefiles["stack_window"])
        self.window = self.obj("app_window")
        ply.prepare_player()
        self.obj("stack").set_visible_child_name(cli.default_app_view)
        if cli.default_app_view == "compact":
            self.obj("radio_compact").set_active(True)
            self.obj("content_wdir_ext").remove(app.obj("treeview_wdir"))
            self.obj("content_wdir_compact").add(app.obj("treeview_wdir"))
        else:
            self.obj("radio_ext").set_active(True)

    def load_application_window(self):
        self.builder.add_from_file(self.gladefiles["main_window"])
        self.window = self.obj("gpwindow")

    def load_player_window(self):
        self.builder.add_from_file(self.gladefiles["main_window_mediaplayer"])
        ply.prepare_player()
        self.window = self.obj("gp_ext_appwindow")

    def set_dialog_relations(self, mainwin, dialog):
        [dialog(d).set_transient_for(mainwin) for d in ("aboutdialog",
                                                        "multwindow",
                                                        "confirm_format_dialog",
                                                        "targetfolderwindow",
                                                        "importmessage")]

    def get_window_content(self):
        """Fill main window with content"""
        self.show_workdir()
        self.load_dircontent()
        self.find_sd()
        self.discspace_info()
        self.obj("act_othloc").set_text(_("(none)"))
        self.obj("import_other").set_sensitive(False)

        # set Kdenlive support menu item inactive when disabled
        self.obj("kd_supp_switch").set_state(cli.kd_supp)

    def show_workdir(self):
        """Show path to working directory"""
        self.obj("act_wdir").set_text(cli.stdir)

    def load_dircontent(self):
        """Display content of working directory with TreeView"""
        # Tabelle leeren, da sonst bei jeder Aktualisierung Zeilen nur angefügt werden
        self.obj("treestore1").clear()
        os.chdir(cli.stdir)
        self.get_tree_data(cli.stdir)
        self.obj("treeview_wdir").expand_all()
        # Buttons auf inaktiv setzen, da sonst Buttons entsprechend der letzten parent-Zeile
        # aktiviert werden
        self.activate_tl_buttons(0, 0, 0, False)

    def get_tree_data(self, directory, parent=None):
        """Creates TreeStore table"""
        for dirs in sorted(os.listdir(directory)):
            path = os.path.join(directory, dirs)
            if os.path.isdir(path):
                os.chdir(dirs)
                # count media
                vidcount = len(glob.glob("*.MP4"))
                imgcount = len(glob.glob("*.JPG"))
                # size of directory, subdiretories exclued
                size = sum([os.path.getsize(f) for f in os.listdir(".") if os.path.isfile(f)])
                humansize = self.sizeof_fmt(size)
                try:
                    # 4th/5th position in file name of last element in sorted list of sequences
                    # (e.g. Seq_03_010.JPG)
                    seq = int(sorted(glob.glob("Seq_*_*.*"))[-1][4:6])
                except:
                    seq = 0
                # transmit row to treestore
                row = self.obj("treestore1").append(parent,
                                                    [dirs, vidcount, imgcount, humansize, path, seq, False, size]
                                                    )
                # read subdirs as child rows
                self.get_tree_data(path, row)
                os.chdir("..")

    def activate_tl_buttons(self, v, i, p, s):
        """Buttons only activated if function is available for file(s)"""
        if v > 0:
            self.obj("tlvideo_button").set_sensitive(True)
        else:
            self.obj("tlvideo_button").set_sensitive(False)
        if i > 1:
            self.obj("tlimage_button").set_sensitive(True)
        else:
            self.obj("tlimage_button").set_sensitive(False)
        if s:
            self.obj("tlimage_sub_button").set_sensitive(True)
        else:
            self.obj("tlimage_sub_button").set_sensitive(False)

    def timelapse_vid(self, p, m):
        """Create video timelapse"""
        # p=path, m=multiplier
        self.refresh_progressbar(0, 1)
        os.chdir(p)
        ctl.makeldir()
        ctl.ffmpeg_vid(p, m)
        self.load_dircontent()

    def timelapse_img(self, p):
        """Create timelapse from images"""
        # p=path
        self.refresh_progressbar(0, 1)
        os.chdir(p)
        ctl.ldir_img(p)
        ctl.ffmpeg_img(p)
        self.load_dircontent()

    def timelapse_img_subfolder(self, p):
        """Create timelapse from images in subfolders"""
        self.refresh_progressbar(0, 1)
        os.chdir(p)
        ctl.makeldir()
        abs_subf = len(glob.glob("Images_1*"))
        counter = 0
        for dirs in sorted(os.listdir(p)):
            if dirs.startswith("Images_1"):
                counter += 1
                cli.show_message(_("Create {} of {}").format(counter, abs_subf))
                self.refresh_progressbar(counter, abs_subf)
                ctl.ffmpeg_img(dirs)
                os.chdir("..")
        self.load_dircontent()

    def refresh_progressbar(self, c, a):
        """Refresh progress bar with current status"""
        fraction = c / a
        try:
            self.obj("progressbar").set_fraction(fraction)
            time.sleep(.1)
            # see  http://faq.pygtk.org/index.py?req=show&file=faq23.020.htp or
            # http://ubuntuforums.org/showthread.php?t=1056823...it, well, works
            while Gtk.events_pending():
                Gtk.main_iteration()
        except:
            raise

    def find_sd(self):
        if cli.detectcard():
            # activate buttons if card is mounted
            self.obj("act_sd").set_text(cli.cardpath)
            self.obj("open_sd").set_sensitive(True)
            self.obj("format_sd").set_sensitive(True)
            self.obj("sd_content_info").set_text(cli.card_content(cli.cardpath))
            if cli.freespace(cli.cardpath, cli.stdir):
                self.obj("import_sd").set_sensitive(True)
            else:
                self.obj("import_sd").set_sensitive(False)
                self.obj("nospace_info").set_text(
                    _("Not enough disc space.\nFree at least {}.").format(cli.needspace))
        else:
            self.obj("act_sd").set_text(_("(none)"))
            self.obj("import_sd").set_sensitive(False)
            self.obj("open_sd").set_sensitive(False)
            self.obj("format_sd").set_sensitive(False)
            self.obj("sd_content_info").set_text("")

    def discspace_info(self):
        """Save memory information about disc and card in list
           [total,used,free], use values to display levelbar and label element below"""

        self.disc_space = [shutil.disk_usage(cli.stdir).total,
                           shutil.disk_usage(cli.stdir).used,
                           shutil.disk_usage(cli.stdir).free,
                           ]
        if cli.detectcard():
            self.card_space = [shutil.disk_usage(cli.cardpath).total,
                               shutil.disk_usage(cli.cardpath).used,
                               shutil.disk_usage(cli.cardpath).free,
                               True,
                               ]
        else:
            self.card_space = [1, 0, 0, False]

        self.disc_bar = self.obj("level_wdir")
        self.card_bar = self.obj("level_sd")

        self.disc_bar.add_offset_value("lower", 0.5)
        self.disc_bar.add_offset_value("low", 0.7)
        self.disc_bar.add_offset_value("high", 0.9)

        self.card_bar.add_offset_value("lower", 0.4)
        self.card_bar.add_offset_value("low", 0.7)
        self.card_bar.add_offset_value("high", 0.9)

        self.disc_bar.set_value(self.disc_space[1] / self.disc_space[0])
        self.card_bar.set_value(self.card_space[1] / self.card_space[0])

        self.obj("free_wdir").set_text(_("free: {0} of {1}").format(
            self.sizeof_fmt(self.disc_space[2]),
            self.sizeof_fmt(self.disc_space[0]),
            ))

        if self.card_space[3]:
            self.obj("free_sd").set_text(_("free: {0} of {1}").format(
                self.sizeof_fmt(self.card_space[2]),
                self.sizeof_fmt(self.card_space[0]),
                ))
        else:
            self.obj("free_sd").set_text("")

    # borrowed from
    # http://stackoverflow.com/questions/1094841/
    # reusable-library-to-get-human-readable-version-of-file-size
    def sizeof_fmt(self, num, suffix="B"):
        """File size shown in common units"""
        for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
            if abs(num) < 1024.0:
                return "%3.1f %s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f %s%s" % (num, "Yi", suffix)

    def get_targetfolderwindow_content(self):
        """Get list for dropdown selection and open window"""
        copyfolder_list = self.obj("destfolder_store")
        copyfolder_list.clear()
        # first row = default folder (today"s date)
        today = time.strftime("%Y-%m-%d", time.localtime())

        copyfolder_list.append([today])

        for d in sorted(os.listdir(cli.stdir)):
            if d != today:
                copyfolder_list.append([d])

        # glade bug: no effects when set in glade
        self.obj("combobox1").set_entry_text_column(0)
        # set first row as default editable entry
        self.obj("combobox1").set_active(0)

        window = self.obj("targetfolderwindow")
        window.show_all()

    def main(self, argv):
        self.app.run(argv)


class GoProPlayer:

    def __init__(self):

        # init GStreamer
        Gst.init(None)

    def prepare_player(self):
        # setting up videoplayer
        self.player = Gst.ElementFactory.make("playbin", "player")
        self.sink = Gst.ElementFactory.make("gtksink")

        # get the widget the gtksink creates and add to box with empty space where the DrawingArea
        # used to be when using the xvimagesink that does not work with HeaderBar (yes, I
        # eliminated the failure)
        video_widget = self.sink.get_property("widget")
        app.obj("video_box").add(video_widget)

        # playpause togglebutton
        self.playpause_button = app.obj("playpause_togglebutton")

        # setting up progress scale
        self.slider = app.obj("progress")
        self.slider_handler_id = self.slider.connect("value-changed", self.on_slider_seek)

    def setup_player(self, f):
        # file to play must be transmitted as uri
        self.uri = "file://" + os.path.abspath(f)
        self.player.set_property("uri", self.uri)

        self.player.set_property("video-sink", self.sink)

    def play(self):
        if self.uri.endswith(".MP4"):
            self.is_playing = True
        else:
            self.is_playing = False
        cli.log.info(_("play"))
        self.player.set_state(Gst.State.PLAYING)

        # starting up a timer to check on the current playback value
        GLib.timeout_add(1000, self.update_slider)

    def pause(self):
        self.is_playing = False
        cli.log.info(_("playback paused"))
        self.player.set_state(Gst.State.PAUSED)

    def current_position(self):
        status, position = self.player.query_position(Gst.Format.TIME)
        return position

    # skip 1 minute on forward/backward button
    def skip_minute(self, direction=1):
        self.player.seek_simple(Gst.Format.TIME,
                                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                self.current_position() + float(60) * Gst.SECOND * direction,
                                )

    def update_slider(self):
        if not self.is_playing:
            return False  # cancel timeout
        else:
            success, self.duration = self.player.query_duration(Gst.Format.TIME)
            # adjust duration and position relative to absolute scale of 100
            try:
                self.mult = 100 / (self.duration / Gst.SECOND)
            except ZeroDivisionError:
                cli.log.exception(_("Exception error"))
            if not success:
                # raise SliderUpdateException("Couldn"t fetch duration")
                cli.log.warning(_("Couldn't fetch duration"))
            # fetching the position, in nanosecs
            success, position = self.player.query_position(Gst.Format.TIME)
            if not success:
                # raise SliderUpdateException("Couldn"t fetch current position to update slider")
                cli.log.warning(_("Couldn't fetch current position to update slider"))

            # block seek handler so we don"t seek when we set_value()
            self.slider.handler_block(self.slider_handler_id)

            self.slider.set_value(float(position) / Gst.SECOND * self.mult)

            self.slider.handler_unblock(self.slider_handler_id)
        return True  # continue calling every x milliseconds

    def on_slider_seek(self, widget):
        if self.uri.endswith(".JPG"):
            seek_intvl = ply.slider.get_value()
            files = len(os.listdir("Images_100"))
            seek_file = int((files * seek_intvl) / 100)
            self.clear_playbin()
            self.setup_player(os.path.join("Images_100",
                                           sorted(os.listdir("Images_100"))[seek_file]),
                              )
            self.pause()
        elif not self.uri.endswith(".png"):
            seek_time = ply.slider.get_value()
            self.player.seek_simple(Gst.Format.TIME,
                                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                    seek_time * Gst.SECOND / self.mult,
                                    )

    def clear_playbin(self):
        try:
            self.is_playing = False
            self.player.set_state(Gst.State.NULL)
        except:
            pass

    def mediainfo(self, f):
        """Get media file information from mediainfo and show in textviewarea"""
        try:
            # getting information from mediainfo and save in variable out
            mediainfo_cmd = subprocess.Popen(["mediainfo", f],
                                             universal_newlines=True,
                                             stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE,
                                             )
            out, err = mediainfo_cmd.communicate()
            out = out.split("\n")

            # filter lines which should be shown
            ginfo = ("Format",
                     "File size",
                     "Duration",
                     "Overall bit rate",
                     "Frame rate",
                     )

            vinfo = ("Format",
                     "Width",
                     "Height",
                     "Display aspect ratio",
                     "Frame rate mode",
                     "Frame count",
                     "Format/Info",
                     "Bit rate mode",
                     "Bit rate",
                     )

            ainfo = ("Format",
                     "Bit rate mode",
                     "Bit rate",
                     )

            iinfo = ("Width",
                     "Height",
                     "Bit depth",
                     )

            # prepare categories for gtk treeview
            mtype = ("General", "Video", "Audio", "Image")
            mtype_info = (ginfo, vinfo, ainfo, iinfo)

            """mediainfo output:

            General
            Format : ...
            ...    : ...
            Video
            ...    : ...
            .
            .
            .
            """

            # create data list
            mediainfo = []
            store = Gtk.TreeStore(str, str, str)

            for line in out:
                # find start of category, reset parent
                if line.strip() in mtype:
                    i = mtype.index(line.strip())
                    mt = line.strip()
                    parent = None
                # split output line
                elif line.find(":") > -1:
                    row = line.split(":", 1)
                    # only use lines defined in the variable above
                    if row[0].strip() in mtype_info[i]:
                        # append to intern list, obsolete in later gtk-only app
                        mediainfo.append([mt, row[0].strip(), row[1]])
                        if parent:
                            store.append(parent, [mt, row[0].strip(), row[1]])
                        else:
                            treeiter = store.append(None, [mt, row[0].strip(), row[1]])
                            parent == treeiter

            # print data list
            mediatext = ""
            for line in mediainfo:
                string = "{0:7} | {1:20} | {2:}".format(line[0], line[1], line[2])
                mediatext += string + "\n"

            # setting up text in monospace does not work anymore, not working in glade either,
            # see css variable for details
            # app.obj("mediainfo_text").set_monospace(True)
            app.obj("textbuffer1").set_text(mediatext)

        except FileNotFoundError:
            cli.show_message(_("MediaInfo is not installed."))
            app.obj("textbuffer1").set_text("MediaInfo is not installed.")


class GoProGo:

    def __init__(self):

        setproctitle.setproctitle("GPT")
        self.install_dir = os.getcwd()
        self.user_app_dir = os.path.join(os.path.expanduser("~"),
                                         ".config",
                                         "gpt",
                                         )
        # create hidden app folder in user"s home directory if it does
        # not exist
        if not os.path.isdir(self.user_app_dir):
            os.makedirs(self.user_app_dir)

        # initiate GTK+ application
        GLib.set_prgname("GPT")

        # set up logging
        os.chdir(self.user_app_dir)
        self.log = logging.getLogger("gpt")
        with open(os.path.join(self.install_dir, "logging.yaml")) as f:
            config = yaml.load(f)
            logging.config.dictConfig(config)

        self.loglevels = {"critical": 50,
                          "error": 40,
                          "warning": 30,
                          "info": 20,
                          "debug": 10,
                          }

        # log version info for debugging
        self.log.debug("Application version: {}".format(__version__))
        self.log.debug("GTK+ version: {}.{}.{}".format(Gtk.get_major_version(),
                                                          Gtk.get_minor_version(),
                                                          Gtk.get_micro_version(),
                                                          ))
        self.log.debug(_("Application executed from {}").format(self.install_dir))

        self.locales_dir = os.path.join(self.install_dir, "po", "locale")
        self.appname = "GPT"

        # setting up localization
        locale.bindtextdomain(self.appname, self.locales_dir)
        locale.textdomain(self.locales_dir)
        gettext.bindtextdomain(self.appname, self.locales_dir)
        gettext.textdomain(self.appname)

        # check for config file to set up working directory
        # create file in case it does not exist
        self.config = os.path.join(self.user_app_dir, "config.py")
        self.defaultwdir = os.path.join(os.path.expanduser("~"), "GP")

        if os.path.isfile(self.config):
            self.readconfig()
        else:
            self.stdir = self.defaultwdir
            self.chkdir(self.stdir)
            self.createconfig(self.stdir)
            self.kd_supp = True

        self.show_message(_("Working directory: {}").format(self.stdir))

    def createconfig(self, wdir):
        """Creates new configuration file and writes current working directory"""

        self.show_message(_("Creating config file..."))
        config = open(self.config, "w")
        config.write("""##### CONFIG FILE FOR GOPRO TOOL #####
##### EDIT IF YOU LIKE. YOU ARE AN ADULT. #####\n""")
        config.close()
        self.write_wdir_config(wdir)
        self.write_kd_supp_config()
        self.default_app_view = "ext"
        self.write_app_view_config(self.default_app_view)

    def write_wdir_config(self, wdir):
        """Write value for working directory to configuration file"""
        config = open(self.config, "a")
        config.write("\n##### working directory #####\nwdir = \"{}\"\n".format(wdir))
        config.close()

    def write_kd_supp_config(self):
        """Default Kdenlive support is enabled and written to configuration file"""
        config = open(self.config, "a")
        config.write("\n##### Kdenlive support #####\nkdsupp = True\n")
        config.close()

    def write_app_view_config(self, appview):
        """Write value for default application window stack page to configuration file"""
        config = open(self.config, "a")
        config.write("\n##### default application view #####\nappview = \"{}\"\n".format(appview))
        config.close()

    def replace_wdir_config(self, wdir):
        """Writes new working directory in config file when changed"""
        for line in fileinput.input(self.config, inplace=True):
            if line.startswith("wdir"):
                sys.stdout.write("wdir = \"{}\"\n".format(wdir))
            else:
                sys.stdout.write(line)

    def change_kd_support_config(self, supp):
        """Changes Kdenlive support in config file when changed (menu item toggled)"""
        for line in fileinput.input(self.config, inplace=True):
            if line.startswith("kdsupp"):
                sys.stdout.write("kdsupp = {}\n".format(supp))
            else:
                sys.stdout.write(line)

    def change_appview_config(self, view):
        """Changes default application stack page in config file when changed
           (menu item toggled)"""
        for line in fileinput.input(self.config, inplace=True):
            if line.startswith("appview"):
                sys.stdout.write("appview = \"{}\"".format(view))
            else:
                sys.stdout.write(line)

    def readconfig(self):
        """Reads working directory and Kdenlive support status (line begins with "wdir = ...")
           from configuration file and tries to apply given value. If this attempt fails (due
           to permission problems) or there is no matching line the default value (~/GP) will
           be set."""
        match_wdir = False
        match_kd = False
        match_view = False
        config = open(self.config, "r")
        for line in config:
            if line.startswith("wdir"):
                match_wdir = True
                self.stdir = line.split("\"")[1]
                if not self.chkdir(self.stdir):
                    self.stdir = self.defaultwdir
                    self.replace_wdir_config(self.stdir)
                continue
            if line.startswith("kdsupp"):
                if line.split("=")[1].strip() == "True":
                    self.kd_supp = True
                    match_kd = True
                elif line.split("=")[1].strip() == "False":
                    self.kd_supp = False
                    match_kd = True
                else:
                    self.change_kd_support_config(True)
                    self.kd_supp = True
                    match_kd = True
                continue
            if line.startswith("appview"):
                if line.split("=")[1].strip() == "compact":
                    self.default_app_view = "compact"
                    match_view = True
                else:
                    self.default_app_view = "ext"
                    match_view = True
                continue
        config.close()
        # add wdir line when not found
        if not match_wdir:
            self.show_message(_("No configuration for working directory in config file. Set default value (~/GP)..."))
            self.stdir = self.defaultwdir
            self.chkdir(self.stdir)
            # write default wdir to config file
            self.write_wdir_config(self.stdir)

        if not match_kd:
            self.show_message(_("Kdenlive support is enabled."))
            self.kd_supp = True
            self.write_kd_supp_config()

        if not match_view:
            self.show_message(_("Default application view is set to extended."))
            self.default_app_view = "ext"
            self.write_app_view_config(self.default_app_view)

    def show_message(self, message, log="info"):
        """Show notifications in terminal window and status bar if possible"""
        try:
            app.obj("statusbar").push(1, message)
            time.sleep(.1)
            while Gtk.events_pending():
                Gtk.main_iteration()
        except (AttributeError, NameError):
            self.log.debug(_("Could not write message to statusbar"))
            self.log.debug(_("Message: {}").format(message))
            # print(message)
        if log in self.loglevels.keys():
            lvl = self.loglevels[log]
        else:
            lvl = 0
        self.log.log(lvl, message)

    # function exclusively called by cli
    def chwdir(self):
        """Setting up working directory, default: ~/GP"""
        while 1:
            befehl = input(_("Change working directory? (y/N) "))
            if befehl == "y":
                newdir = input(_("Input path: "))
                if newdir == "":
                    self.show_message(_("No change."))
                    break
                else:
                    self.chkdir(newdir)
                    self.stdir = os.getcwd()
                    self.replace_wdir_config(newdir)
                    break
            elif befehl == "n" or befehl == "":
                self.show_message(_("Everything stays as it is."))
                break
            else:
                self.show_message(_("Invalid input"))

    # function exclusively called by cli
    def handlecard(self):
        if self.detectcard() is True:
            self.card_content(self.cardpath)
            while 1:
                befehl = input(_("Memory card found. Copy and rename media files to working directory? (y/n) "))
                if befehl == "n":
                    print(_("Move along. There is nothing to see here."))
                    break
                elif befehl == "y":
                    if self.freespace(self.cardpath, self.stdir):
                        self.copycard(self.cardpath,
                                      os.path.join(self.stdir,
                                                   self.choosecopydir(self.stdir),
                                                   ),
                                      )
                        break
                    else:
                        print(_("Failed to copy files. Not enough free space."))
                        break
                else:
                    print(_("Invalid input"))

    def detectcard(self):
        """Find mounted memory card"""

        # list of possible paths to temporary storage
        userdrive = [os.path.join("/run", "media", getpass.getuser()),
                     os.path.join("/media", getpass.getuser()),
                     ]
        for path in userdrive:
            try:
                os.chdir(path)
                for d in os.listdir():
                    os.chdir(d)
                    self.show_message(_("Search in {}").format(d))
                    if "Get_started_with_GoPro.url" in os.listdir():
                        self.subpath_card = "DCIM"
                        self.cardpath = os.path.join(path, d)
                        self.show_message(_("Found GoPro device."))
                        return True
                    elif os.path.exists(os.path.join(os.getcwd(),
                                                     "PRIVATE",
                                                     "SONY",
                                                     "SONYCARD.IND")
                                        ):
                        self.subpath_card = "MP_ROOT"
                        self.cardpath = os.path.join(path, d)
                        self.show_message(_("Found Sony device."))
                        return True
                    else:
                        self.show_message(_("Device is not supported."))
                    os.chdir("..")
                # wieder ins ursprüngliche Arbeitsverzeichnis wechseln
                self.workdir(self.stdir)
                return False
            except FileNotFoundError:
                self.show_message(_("No device found in path {}.").format(path))
                return False

    # collect content information of plugged memory card
    def card_content(self, path):
        print(_("Card mount point:"), path)
        # search for files
        vid_count = 0
        img_count = 0
        vid_size = 0
        img_size = 0
        for root, dirs, files in os.walk(path):
            for filename in files:
                if filename.endswith(".MP4"):
                    vid_count += 1
                    vid_size += os.path.getsize(os.path.join(root, filename))
                elif filename.endswith(".JPG"):
                    img_count += 1
                    img_size += os.path.getsize(os.path.join(root, filename))

        info = _("Number of videos: {}, total size: {}\nNumber of images: {}, total size: {}").format(vid_count,
                                                                                                      app.sizeof_fmt(vid_size),
                                                                                                      img_count,
                                                                                                      app.sizeof_fmt(img_size),
                                                                                                     )
        print(info)
        # (just for clarity) set separate variable for progress bar use
        self.abs_vid = vid_count
        self.abs_img = img_count
        self.abs_size = vid_size + img_size
        return info

    def copycard(self, mountpoint, targetdir):
        """Copy media files to target folder in working directory and rename them"""
        self.chkdir(targetdir)
        self.show_message(_("Copy files from {} to {}.").format(mountpoint, targetdir))
        self.copymedia(os.path.join(mountpoint, self.subpath_card), targetdir)
        self.show_message(_("Files successfully copied."))
        os.chdir(targetdir)
        for path, dirs, files in os.walk(targetdir):
            os.chdir(path)
            self.sortfiles()
        self.show_message(_("Done."))
        os.chdir(mountpoint)

    # Speicherplatz analysieren
    def freespace(self, src, dest):
        """Check for free disc space"""
        import_size = 0
        for dirpath, dirnames, filenames in os.walk(src):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                import_size += os.path.getsize(fp)
        if import_size < shutil.disk_usage(dest).free:
            return True
        else:
            self.needspace = app.sizeof_fmt(import_size - shutil.disk_usage(dest).free)
            return False

    # Zielordner wählen, neuen oder bestehenden Ordner, Defaultwert yyyy-mm-dd
    def choosecopydir(self, wdir):
        os.chdir(wdir)
        # default folder name is today"s date
        default = time.strftime("%Y-%m-%d", time.localtime())
        self.copydirlist = []
        counter = 0
        for d in os.listdir(wdir):
            # get folders in directory without hidden
            if os.path.isdir(d) and not d.startswith("."):
                counter += 1
                self.copydirlist.append([counter, d])
        if counter > 0:
            print(_("(project) folders in working directory"))
            print("**************************************")
            print("--> {0:^6} | {1:25}".format(_("no."), _("name")))
            for n in self.copydirlist:
                print("--> {0:^6} | {1:25}".format(n[0], n[1]))
        else:
            print(_("There are no subfolders in the working directory yet"))
        return self.copydir_prompt(default, counter)

    def copydir_prompt(self, default, c):
        """Value returned is name of default or selected subfolder"""
        if c == 0:
            return default
        while 1:
            try:
                prompt = input(_("Choose destination folder (return for default value: {}): ").format(default))
                if prompt == "":
                    return default
                elif int(prompt) > c or int(prompt) < 1:
                    print(_("Invalid input, input must be integer between 1 and {}. Try again...").format(c))
                else:
                    return self.copydirlist[int(prompt)-1][1]
            except ValueError:
                print(_("Invalid input (integer required). Try again..."))

    # Medien kopieren
    def copymedia(self, src, dest):
        """Copy files from card to working directory (preview files excluded)"""
        os.chdir(src)

        # absolute number of all files being copied
        abs_files = self.abs_vid + self.abs_img
        counter = 0

        # reset progressbar
        app.refresh_progressbar(0, 1)

        # copy files of subdirectories
        # for d in os.listdir():
        for d, *args in os.walk(os.getcwd()):
            os.chdir(d)
            self.show_message(_("Changed directory to {}").format(d))
            time.sleep(.1)

            # for easy handling keep pictures in subfolders analogue to source file structure
            # create subfolder for image sequences
            if glob.glob("*.JPG"):
                self.show_message(_("Found photos..."))
                self.chkdir(os.path.join(dest, "Images_" + d[0:3]))
                self.workdir(os.path.join(src, d))

            # #### preparations for video files #####
            # create empty list for threads used for copying video files
            thread_list = []
            # number of videos in subdirectory
            vid_counter = [v.count(".MP4") for v in os.listdir()].count(1)
            # this probably needs some explanation:
            # this list is used to show the threads remaining to be finished, not the active_count
            # because there are only max 3 active threads when copying video files; I"m open for a
            # clean solution here but as long at this works for me this will last -
            # nothing is as duable as a makeshift...
            self.thread_counter = []
            [self.thread_counter.append("x") for i in range(vid_counter)]

            for f in sorted(os.listdir()):
                # image files
                if f.endswith(".JPG"):
                    shutil.copy(f, os.path.join(dest, "Images_" + d[0:3]))
                    counter += 1
                    self.show_message(_("{} copied ({}/{})").format(f, counter, abs_files))
                    app.refresh_progressbar(counter, abs_files)

                # video files
                if f.endswith(".MP4"):
                    t = threading.Thread(target=self.copyvid_thread, args=(f, dest, abs_files,))
                    # prepare threads
                    thread_list.append(t)

            for thread in thread_list:
                thread.start()
                while Gtk.events_pending():
                    Gtk.main_iteration()
                thread.join()   # wait until thread is finished

            counter += vid_counter
            os.chdir("..")

        self.show_message(_("Copying files finished."))
        app.refresh_progressbar(1, 1)

    def copyvid_thread(self, f, dest, abs_files):
        shutil.copy(f, dest)
        self.thread_counter.pop()
        self.show_message(_("{} copied ({}/{})").format(f, abs_files - len(self.thread_counter), abs_files))
        app.refresh_progressbar(abs_files - len(self.thread_counter), abs_files)

    # Verzeichnisse anlegen, wenn möglich, falls nicht, Fallback in vorheriges Arbeitsverzeichnis
    # Gebrauch: Initialisierung/Änderung des Arbeitsverzeichnisses, Erstellung von Unterordnern
    # vor Kopieren der Speicherkarte (Abfrage, um eventuelle Fehlermeldung wegen bereits
    # vorhandenen Ordners zu vermeiden)
    def chkdir(self, path):
        """Create folder if nonexistent, check for write permission then change into directory"""
        try:
            os.makedirs(path)
            self.show_message(_("Folder created."))
            self.workdir(path)
            return True
        except OSError as exception:
            if exception.errno == errno.EEXIST:
                self.show_message(_("Directory already exists. OK."))
                if os.access(path, os.W_OK):
                    self.workdir(path)
                else:
                    self.show_message(_("Error: no write permission"))
                    self.workdir(self.stdir)
                return True
            elif exception.errno == errno.EACCES:
                self.show_message(_("Permission denied."))
                return False
            else:
                self.show_message(_("Invalid path"))
                self.workdir(self.stdir)
                return True

    # Verzeichnis wechseln
    def workdir(self, path):
        """Change directory"""
        self.show_message(_("Change directory to {}").format(path))
        os.chdir(path)

    def sortfiles(self):
        """Save video files in (chrono)logical order. Photos are seperated by single shots and
           sequences. FFmpeg explicitly requires file numbering in "%d" format for timelapse
           creation. GoPro saves a maximum of 999 files per subfolder so 001.JPG..00n.JPG is
           sufficient"""

        # Video
        if glob.glob("GP*.MP4") or glob.glob("GOPR*.MP4"):
            message = _("{} video file(s) will be renamed.").format(len(glob.glob("GP*.MP4")) + len(glob.glob("GOPR*.MP4")))
            self.show_message(message)
            for f in glob.glob("GP*.MP4"):
                newfile = "gp" + f[4:8]+f[2:4] + ".MP4"
                os.rename(f, newfile)
            for f in glob.glob("GOPR*.MP4"):
                newfile = "gp" + f[4:8] + "00.MP4"
                os.rename(f, newfile)
        else:
            if glob.glob("*.MP4") or glob.glob("*.mp4"):
                self.show_message(_("Video files do not match the GoPro naming convention. No need for renaming or renaming already done."))
            else:
                self.show_message(_("No video files."))

        # detect existing sequences
        # TODO use treeview seq column instead
        if glob.glob("Seq_*.MP4"):
            seq = int(sorted(glob.glob("Seq_*.MP4"))[-1][4:6])
        else:
            seq = 0

        # save in sequences (see image section below), pattern: Seq_0n_0n.MP4
        for f in sorted(glob.glob("gp*.MP4")):
            if f.endswith("00.MP4"):
                seq += 1
            newfile = "Seq_{0:02d}_{1}.MP4".format(seq, f[6:8])
            os.rename(f, newfile)

        # Foto
        # pattern for sequences: Seq_0n_00n.JPG, single shots: Img_00n.JPG
        if glob.glob("G0*.JPG") or glob.glob("GOPR*.JPG"):
            # Einzelbilder
            message = _("{} image files will be renamed.").format(len(glob.glob("G*.JPG")) + len(glob.glob("GOPR*.JPG")))
            self.show_message(message)
            counter = 1
            for f in sorted(glob.glob("GOPR*.JPG")):
                newfile = "Img_%03d.JPG" % counter
                os.rename(f, newfile)
                counter += 1
            # counter for files
            counter = 1
            # sequence number can be extracted from file name
            seq = sorted(os.listdir())[0][2:4]
            for f in sorted(glob.glob("G0*.JPG")):
                if f[2:4] == seq:
                    newfile = "Seq_" + seq + "_%03d.JPG" % counter
                else:
                    counter = 1
                    seq = f[2:4]
                    newfile = "Seq_" + seq + "_%03d.JPG" % counter
                os.rename(f, newfile)
                counter += 1
        else:
            if glob.glob("*.JPG"):
                self.show_message(_("Image files do not match the GoPro naming convention. No need for renaming or renaming already done."))
            else:
                # andere Formate etc.
                self.show_message(_("No matching image files."))

    def confirm_format(self):
        if self.detectcard():
            while 1:
                befehl = input(_("Are you sure to remove all files from media card? (y/n) "))
                if befehl == "y":
                    self.format_sd()
                    break
                elif befehl == "n":
                    break
                else:
                    print(_("Invalid input. Try again..."))

    def format_sd(self):
        print(_("Delete files in {}...").format(self.cardpath))
        os.chdir(self.cardpath)
        for f in os.listdir():
            if os.path.isfile(f):
                try:
                    os.remove(f)
                    self.show_message(_("{} deleted.").format(f))
                except:
                    self.show_message(_("Failed to delete file. Check permissions."))
                    raise
            elif os.path.isdir(f):
                try:
                    shutil.rmtree(f)
                    self.show_message(_("{} deleted.").format(f))
                except:
                    self.show_message(_("Failed to delete directory. Check permissions."))
                    raise
        self.workdir(self.stdir)

    # Dateien löschen, obsolet, siehe Vorschaudateien
    def delfiles(self, ftype):
        """Dateien bestimmten Typs löschen"""
        while 1:
            print()
            befehl = input(_("Delete (y/n) "))
            if befehl == "y":
                for file in os.listdir(self.dir):
                    if file.endswith(ftype):
                        self.show_message(_("Deleting {}.").format(file))
                        os.remove(file)
                break
            elif befehl == "n":
                break
            else:
                self.show_message(_("Invalid input. Try again..."))

    # Menü
    def help(self):
        """Serving the menu..."""
        print(_("""
        (h)elp
        ------------- working directory ----------
        {}
        
        ------------- routines -------------------
        change (w)orking directory
        detect (c)ard
        (r)ead directory and rename GoPro files
        (d)elete all files on external memory card

        ------------- create ---------------------
        timelapse from (v)ideo
        timelapse from (i)mages
        (k)denlive project 

        (q)uit""").format(self.stdir))

    def shell(self):
        """Input prompt"""
        while 1:
            print()
            befehl = input()
            if befehl == "h" or befehl == "":
                self.help()
            elif befehl == "r":
                self.sortfiles()
            elif befehl == "c":
                self.handlecard()
            elif befehl == "w":
                self.chwdir()
            elif befehl == "d":
                self.confirm_format()
            elif befehl == "v":
                ctl.countvid()
            elif befehl == "i":
                ctl.countimg()
            elif befehl == "k":
                kds.countvid()
            elif befehl == "q":
                break
            else:
                print(_("Invalid input. Try again..."))


class KdenliveSupport:

    def __init__(self):

        self.wdir = os.getcwd()

    def create_project(self, folder):

        # load Kdenlive template without clips for later project file generation
        with open(os.path.join(cli.install_dir,
                               "kdenlive-template.xml",
                               ),
                  "r") as f:
            self.tree = etree.parse(f)
        self.root = self.tree.getroot()
        self.mainbin = self.tree.find("playlist")  # returns first match

        # use default profile from kdenlive config
        # avoid UnicodeDecodeError when reading file by using codecs package
        with codecs.open(os.path.join(os.path.expanduser("~"),
                                      ".config",
                                      "kdenliverc"),
                         "r",
                         encoding="utf-8",
                         errors="ignore"
                         ) as f:
            for line in f.readlines():
                if "default_profile" in line:
                    kdenlive_profile = line[16:-1]
                    cli.show_message(_("Found default profile: {}").format(kdenlive_profile))
                    break

        profile = etree.SubElement(self.mainbin, "property")
        profile.set("name", "kdenlive:docproperties.profile")
        profile.text = kdenlive_profile

        os.chdir(folder)

        # remove old kdenlive project file if existing
        try:
            os.remove("mlt-playlist.kdenlive")
            cli.show_message(_("Delete old Kdenlive project file."))
        except FileNotFoundError:
            cli.show_message(_("No existing Kdenlive project file to remove."))

        # add mediafiles
        counter = 1
        for f in sorted(glob.glob("*.MP4")):
        # for f in sorted(os.listdir()):
            newprod = etree.SubElement(self.root, "producer")
            newprod.set("id", str(counter))
            newprop = etree.SubElement(newprod, "property")
            newprop.text = os.path.join(folder, f)
            newprop.set("name", "resource")
            # insert lines after root tag, otherwise kdenlive crashes at start
            self.root.insert(0, newprod)
            newentry = etree.SubElement(self.mainbin, "entry")
            newentry.set("producer", str(counter))
            counter += 1

        # save as new file
        self.tree.write("mlt-playlist.kdenlive")
        cli.show_message(_("Open Kdenlive project"))
        # open Kdenlive as separate thread to keep GPT responsive
        thread = threading.Thread(target=self.openproject,
                                  args=("kdenlive", "mlt-playlist.kdenlive"))
        thread.start()
        cli.workdir(self.wdir)

    def openproject(self, application, filename):
        subprocess.run([application, filename])

    def countvid(self):
        """Find video files in directory"""
        self.wherevid = []
        counter = 0
        for path, dirs, files in sorted(os.walk(self.wdir)):
            os.chdir(path)
            if len(glob.glob("*.MP4")) > 0:
                counter += 1
                self.wherevid.append([counter, path, len(glob.glob("*.MP4"))])
        if counter > 0:
            print(_("""
Video:
******"""))
            print("--> {0:^6} | {1:50} | {2:>}".format(_("no."),
                                                       _("directory"),
                                                       _("quantity"),
                                                       ))
            for n in self.wherevid:
                print("--> {0:^6} | {1:50} | {2:>4}".format(n[0], n[1], n[2]))
            self.choosevid(counter)
        else:
            print(_("No video files found."))

    def choosevid(self, c):
        """Create and open Kdenlive project file for selected folder"""
        while 1:
            try:
                befehl = int(input(_("Select directory to create and open Kdenlive project (0 to cancel): ")))
                if befehl == 0:
                    break
                elif befehl > c or befehl < 0:
                    print(_("Invalid input, input must be integer between 1 and {}. Try again...").format(c))
                else:
                    message = _("Processing Kdenlive project for {}").format(self.wherevid[befehl-1][1])
                    cli.show_message(message)
                    self.create_project(self.wherevid[befehl-1][1])
                    break
            except ValueError:
                print(_("Invalid input (no integer). Try again..."))


class TimeLapse:

    def __init__(self):
        self.wdir = os.getcwd()

    def makeldir(self):
        """Create folder for timelapses"""
        try:
            os.makedirs("lapse")
            cli.show_message(_("Folder created."))
        except:
            cli.show_message(_("Folder already exists. OK."))
        self.ldir = os.path.join(self.wdir, "lapse")

    def countvid(self):
        """Find video files in directory"""
        self.wherevid = []
        counter = 0
        for path, dirs, files in sorted(os.walk(self.wdir)):
            os.chdir(path)
            if len(glob.glob("*.MP4")) > 0:
                counter += 1
                self.wherevid.append([counter, path, len(glob.glob("*.MP4"))])
        if counter > 0:
            print(_("""
Video:
******"""))
            print("--> {0:^6} | {1:50} | {2:>}".format(_("no."),
                                                       _("directory"),
                                                       _("quantity"),
                                                       ))
            for n in self.wherevid:
                print("--> {0:^6} | {1:50} | {2:>4}".format(n[0], n[1], n[2]))
            self.choosevid(counter)
        else:
            print(_("No video files found."))

    def choosevid(self, c):
        """Create timelapse video for all video files in selected folder"""
        while 1:
            try:
                befehl = int(input(_("Select directory to create timelapse video of (0 to cancel): ")))
                if befehl == 0:
                    break
                elif befehl > c or befehl < 0:
                    print(_("Invalid input, input must be integer between 1 and {}. Try again...").format(c))
                else:
                    message = _("Create timelapse for directory {}.").format(self.wherevid[befehl-1][1])
                    cli.show_message(message)
                    self.choosemult(self.wherevid[befehl-1][1])
                    break
            except ValueError:
                print(_("Invalid input (no integer). Try again..."))

    def choosemult(self, path):
        """Specify multiplier for timelapse video."""
        os.chdir(path)
        self.makeldir()
        while 1:
            try:
                mult = float(input(_("Multiplier: ")))
                if mult == 0:
                    break
                elif mult <= 1:
                    print(_("Multiplier must be larger than 1."))
                else:
                    self.ffmpeg_vid(path, mult)
                    break
            except ValueError:
                print(_("Invalid input (no number). Try again..."))

    def ffmpeg_vid(self, path, m):
        """Let FFmpeg compute timelapse from video"""
        os.chdir(path)
        cli.show_message(_("Create timelapse videos..."))
        self._thread_list = []
        # add progressbar pulse to threads
        t = threading.Thread(target=self._pulse_thread)
        self._thread_list.append(t)

        abs_vid = len(glob.glob("*MP4"))
        for f in glob.glob("*.MP4"):
            # converted from bash script
            # ffmpeg -i $file -r 30 -filter:v "setpts=1/$1*PTS" -an lapse/${file:0:-4}-x$1.MP4
            filename = os.path.join("lapse", f[0:-4] + "-x" + str(m) + ".MP4")
            speed = "setpts=1/" + str(m) + "*PTS"
            command = ["ffmpeg",
                       "-y",
                       "-i", f,
                       "-r", "30",
                       "-filter:v", speed,
                       "-an",
                       "-nostats",
                       "-loglevel", "0",
                       filename]
            t = threading.Thread(target=self._create_timelapse, args=(command,))
            self._thread_list.append(t)
        # this will start the threads but itself is a thread to avoid mainloop blocking
        t = threading.Thread(target=self._start_threads, args=(self._thread_list,))
        t.start()

    def _create_timelapse(self, command):
        subprocess.run(command)

    def _pulse_thread(self):
        while threading.active_count() > 2:
            app.obj("progressbar").pulse()
            time.sleep(.1)
        app.obj("progressbar").set_fraction(1)
        cli.show_message(_("Done."))

    def _start_threads(self, threads):
        for thread in threads:
            thread.start()  # first run will start the progressbar pulse
            while threading.active_count() > 3: # only run one ffmpeg job at a time
                time.sleep(10)

    def countimg(self):
        """Find image files in directory"""
        self.whereimg = []
        counter = 0
        for path, dirs, files in sorted(os.walk(self.wdir)):
            os.chdir(path)
            if len(glob.glob("*.JPG")) > 0:
                counter += 1
                self.whereimg.append([counter, path, len(glob.glob("*.JPG"))])
        if counter > 0:
            print(_("""
Images:
*******"""))
            print("--> {0:^6} | {1:50} | {2:>}".format(_("no."),
                                                       _("directory"),
                                                       _("quantity"),
                                                       ))
            for n in self.whereimg:
                print("--> {0:^6} | {1:50} | {2:>4}".format(n[0], n[1], n[2]))
            self.chooseimg(counter)
        else:
            print(_("No photos found."))

    # TODO: merge with choosevid, almost identical
    def chooseimg(self, c):
        """Create timelapse video(s) for all image files in selected directory"""
        while 1:
            try:
                befehl = int(input(_("Select directory to create timelapse video of (0 to cancel): ")))
                if befehl == 0:
                    break
                elif befehl > c or befehl < 0:
                    print(_("Invalid input, input must be integer between 1 and {}. Try again...").format(c))
                else:
                    print(_("Create timelapse for directory {}").format(self.whereimg[befehl-1][1]))
                    self.ldir_img(self.whereimg[befehl-1][1])
                    self.ffmpeg_img(self.whereimg[befehl-1][1])
                    break
            except ValueError:
                print(_("Invalid input (no integer). Try again..."))

    def ldir_img(self, path):
        """Create timelapse folder for photo timelapses"""
        os.chdir(path)
        # Bei Foto-Timelapses ein Verzeichnis darüber speichern, da ansonsten immer extra lapse-Verzeichnis mit einem Video darin erstellt wird, einfach, aber unelegant...
        os.chdir("..")
        self.makeldir()
        os.chdir(path)

    def ffmpeg_img(self, path):
        """Let FFmpeg compute one timelapse per image sequence with 30 fps and original resolution"""
        # 30 fps works for me because I choose a suitable interval depending on record length and purpose.
        # Here I just want this kind of "raw" video which needs additional cropping to fit into widescreen
        # format and probably some color enhancement, background music etc. so a video editing program is mandatory anyway.
        os.chdir(path)
        self._thread_list = []
        t = threading.Thread(target=self._pulse_thread)
        self._thread_list.append(t)

        try:
            seq = int(sorted(glob.glob("Seq_*_*.JPG"))[-1][4:6])
            for s in range(1, seq+1):
                # converted from bash script
                # ffmpeg -f image2 -r 30 -i %04d.jpg -r 30 ../lapse/$dir.MP4
                f = "Seq_%02d_" % s + "%03d.JPG"
                filename = os.path.join("..", "lapse", "Seq_%02d_%s.MP4" % (s, path[-2:]))
                command = ["ffmpeg",
                           "-y",
                           "-f", "image2",
                           "-r", "30",
                           "-i", f,
                           "-r", "30",
                           "-nostats",
                           "-loglevel", "0",
                           filename,
                           ]
                # this will start the threads but itself is a thread to avoid mainloop blocking
                t = threading.Thread(target=self._create_timelapse, args=(command,))
                self._thread_list.append(t)
            t = threading.Thread(target=self._start_threads, args=(self._thread_list,))
            t.start()
        except IndexError:
            cli.show_message(_("Image files are not a sequence."))


class TimelapseCalculator:

    def __init__(self):

        # data for dropdown menus saved in liststores
        resolution_data = [("5MPsilv", "5 MP (2624x1968) - 3+Silver", 1800000),
                           ("7MPsilv", "7 MP (3072x2304) - 3+Silver", 2300000),
                           ("10MPsilv", "10 MP (3680x2760) - 3+Silver", 3000000),
                           ("5MPsess", "5 MP (2720x2040) - Session", 2200000),
                           ("8MPsess", "8 MP (3264x2448) - Session", 2800000)
                           ]
        interval_data = [("2 photos per second", 120),
                         ("1 photo per second", 60),
                         ("2 seconds interval", 30),
                         ("5 seconds interval", 12),
                         ("10 seconds interval", 6),
                         ("30 seconds interval", 2),
                         ("60 seconds interval", 1),
                         ]

        # create liststore rows
        for d in resolution_data:
            app.obj("list_res").append([d[0], d[1], d[2]])

        for d in interval_data:
            app.obj("list_intvl").append([d[0], d[1]])

        # set first entry as value
        app.obj("combobox_res").set_active(0)
        app.obj("combobox_intvl").set_active(0)

        self.filenum_label = app.obj("filenum_label")
        self.memory_label = app.obj("memory_label")
        self.tl_dur_label = app.obj("tl_dur_label")

        self.fsize = self.get_combobox_data(app.obj("combobox_res"), 2)
        self.intvl = self.get_combobox_data(app.obj("combobox_intvl"), 1)

        self.dur_hours = self.get_spinbutton_data(app.obj("spin_hours"))
        self.dur_min = self.get_spinbutton_data(app.obj("spin_minutes"))
        self.fps = self.get_spinbutton_data(app.obj("spin_fps"))

        self.set_fileinfo()

    def get_combobox_data(self, widget, list_col):
        row = widget.get_active_iter()
        model = widget.get_model()
        return int(model[row][list_col])

    def get_spinbutton_data(self, widget):
        return int(widget.get_value())

    def set_fileinfo(self):
        files = (self.dur_hours * 60 + self.dur_min) * self.intvl
        size = files * self.fsize
        tl_dur = files // self.fps

        self.filenum_label.set_text(str(files))
        self.memory_label.set_text(app.sizeof_fmt(size))
        self.tl_dur_label.set_text("{} min {} s".format(tl_dur // 60, tl_dur % 60))

    def standalone(self):
        window = app.obj("tl_calc_win")
        window.connect("delete-event", Gtk.main_quit)
        app.builder.connect_signals(Handler())
        window.show_all()
        Gtk.main()


cli = GoProGo()
ctl = TimeLapse()
kds = KdenliveSupport()
app = GoProGUI()
ply = GoProPlayer()
tlc = TimelapseCalculator()
