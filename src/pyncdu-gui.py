#!/usr/bin/env python3
from cgitb import text
from genericpath import isdir, isfile
import json
from logging import config
import pprint
import os
import subprocess
#import sys
import psutil
from pathlib import Path
import datetime as dt
from datetime import datetime
import pwd
from functools import total_ordering

import tkinter as tk
import tkinter.ttk as ttk

import time
import logging
import argparse
import sys
import tempfile

import configparser
import logging, logging.config



class IOutil:
    """Various utilities functions"""
    @classmethod
    def readArgs(cls):
        """Read the program arguments"""
        parser = argparse.ArgumentParser(
                    description = 'Wrapper over ncdu command, browse the folders reported by ncdu',
                    epilog = 'marcel_preda@yahoo.com', 
                    formatter_class = argparse.RawTextHelpFormatter)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("-s", "--scan", metavar="/path/to/folder", 
                help = "Folder to be scanned by ncdu command")        
        group.add_argument("-l", "--load", metavar="/path/to/file.json",
                help = "File generated previously with 'ncdu -x -e -o ...' command")
        
        parser.add_argument("-c", "--config", metavar="cfg.ini", required=False,
                help= "Path to config.ini file where we keep the externall command templates.\n" +
                        "If not provided the default one is used, same path with {}".format(
                                sys.argv[0]))
        parser.add_argument("-x", "--exclude", metavar="pattern", required=False,
                help = "Exclude files/folders matching 'pattern'. check ncdu documentation.\n" +
                        "Should be used only with '-s', if '-s' not provided '-x' is ignored.")
        parser.add_argument("-v", "--verbose", required=False, action="store_true",
                help="Increase verbosity level")
        args = parser.parse_args()
        return args
    

# total_ordering because we need to sort
@total_ordering
class FileInfo:
    """
    Store here information about a specific file or folder.
    It should have attributes like:
        - name
        - asize - actuall size
        - dsize - size on disk
        - owner - who owns the file/folder
    """
    # class static memebers
    # a counter to show progress when parsing files
    files_counter = 0
    # keep track of all files owners, usernames are the keys
    files_owners = {}
    #selected owner, the files will be sorted by this
    selected_owner = "*" # it means all
    percent_batch_size = 1
    files_number = 100

    def __init__(self, parent, **kwargs):
        """
        Populate the current file details and all the sub tree if the file is a folder.
        """
        if kwargs.get("name", None) is None:
            self.name = None
            return
        # here we will add the sub tree, if folder
        self.children = []
        self.name = kwargs["name"]
        # when file size is 0 there is no asize attribute
        self.asize = kwargs.get("asize", 0)
        # if symlink it has no dsize, so use asize        
        self.dsize = kwargs.get("dsize", self.asize)
        self.path = os.path.join(parent, kwargs["name"])        
        self.uid = kwargs["uid"]      
        # when look for owner we may ger exxcption like file was deleted 
        # or it is a symlink to a file where we have no access
        self.owner = "*"
        try:
            self.owner = FileUtils.get_username_by_uid(self.uid)
        except:
            pass
        
        self.__class__.files_owners[self.owner] = True        
        # show progress
        cls = self.__class__
        cls.files_counter += 1
        if (cls.files_counter % cls.percent_batch_size) == 0:
            percentage = cls.files_counter*100/cls.files_number
            logger.debug("{:-7d}/{} ({:-6.2f}%) {}".format(
                    cls.files_counter, cls.files_number, percentage, datetime.now()))

    @classmethod
    def set_percent_batch_size(cls, files_number):
        cls.files_number = files_number
        cls.percent_batch_size = min(100000 , round(files_number/10))
    
    def __repr__(self):
        children_str = ""
        for c in self.children:
            children_str += "\n" + c.__repr__()
        self_str = "{}\n\tasize : {}\n\tdsize : {}\n\tpath : {}\n\towner : {}\n\t#children : {}".format(
            self.name, self.asize, self.dsize, self.path, self.owner, len(self.children)        
        )
        return (self_str + children_str)
    
    def add_child(self, child : "FileInfo") :
        """ Add a child, it make sense for a folder to have childs
        """
        self.children.append(child)

    def add_children(self , data_list: list):
        """ 
        Add multiple children derived from a nested structure of the lists with dictionaries
        It will be a recursive function call, to construct the entire file system tree        
        """
        for e in data_list:
            # when list if means there is a folder, 
            # first element being a folder, and the rest of the list are the files/folders contained there
            # if there are nodes with "exclude" they are exclude by ncdu , before various reasons
            # if someting went wrong when retriveing files the "uid" may not be set, 
            # if no "uid" then exclude teh respective entry
            if type(e) is list:
                if e[0].get("uid", None) is None:
                    continue
                child = FileInfo(self.path, **e[0])                          
                child.add_children(e[1:])
                self.add_child(child)            
            elif e.get("excluded", False) == False:
                    if e.get("uid", None) is None:
                        continue
                    child = FileInfo(self.path, **e)
                    self.add_child(child)
        
    def get_hierarchy_size_by_owner(self, owner = "*"):
        """
        Returns the disk szie take by the respective file/folder and the children if it is a folder
        for owner
        """
        hier_size = 0
        if (owner == "*" or self.owner == owner ):
            hier_size += self.dsize
        for c in self.children:
            hier_size += c.get_hierarchy_size_by_owner(owner)
        return hier_size

    def get_hierarchy_size(self):
        """
        Returns the disk szie take by the respective file/folder and the children if it is a folder        
        """
        hier_size = self.dsize
        for c in self.children:
            hier_size += c.get_hierarchy_size()
        return hier_size
    
    # total ordering functions,for sorting objects of this class
    def __lt__(self, other):
        owner = self.__class__.selected_owner
        return self.get_hierarchy_size_by_owner(owner) < other.get_hierarchy_size_by_owner(owner)

    def __gt__(self, other):
        owner = self.__class__.selected_owner
        return self.get_hierarchy_size_by_owner(owner) > other.get_hierarchy_size_by_owner(owner)

    def __eq__(self, other):
        owner = self.__class__.selected_owner
        return self.get_hierarchy_size_by_owner(owner) == other.get_hierarchy_size_by_owner(owner)

    def sort_children_by_size_group_by_selected_owner(self, hier_level):
        self.children.sort(reverse=True)
        # print info messages only on top, not on every recursive call
        if hier_level == 0:
            logger.info("Calculating hierarchy size for user {}...".format(self.get_selected_owner()))        
        for c in self.children:
            c.sort_children_by_size_group_by_selected_owner(hier_level+1)
        if hier_level == 0:
            logger.info("End calculating hierarchy size.")

    def set_selected_owner(self, owner: str):
        self.__class__.selected_owner = owner

    def get_selected_owner(self):
        return self.__class__.selected_owner

    def get_files_owners(self):
        return sorted(self.__class__.files_owners.values())


class FileUtils:
    """
    Files Utility - various files utilities
    """
    # read 1MB chunks, it seems to be the fastest way to read from file
    buf_size = 1024 * 1024

    # cahe here uid -> username, to not make many calls to system 
    # calling pwd.getpwuid(uid) means also IO operations => they are slow
    cache_dict_uid_to_username = {}
    logger : logging.Logger = None

    @classmethod
    def set_logger(cls, logger: logging.Logger) -> None:
        cls.logger  = logger
        

    @classmethod
    def get_file_lines_number(cls, filename):
        """
        Count faster the lines number
        """
        f = open(filename, errors="ignore")
        lines = 0        
        read_f = f.read # loop optimization

        buf = read_f(cls.buf_size)
        while buf:
            lines += buf.count('\n')
            buf = read_f(cls.buf_size)
        f.close()
        return lines

    @classmethod
    def get_username_by_uid(cls, uid ):
        """
        Parameters
        uid : int
            Get sername associated with uid

        Returns
        -------
            string

        """
        uname = cls.cache_dict_uid_to_username.get(uid, False)
        # if uname was not scanned get it from the system and cache it
        if uname == False:            
            uname = pwd.getpwuid(uid).pw_name
            cls.cache_dict_uid_to_username[uid] = uname
        return uname

    @classmethod
    def get_all_usernames(cls) -> list:
        ret_list = list(cls.cache_dict_uid_to_username.values())
        ret_list.sort()
        # insert "*" which means all users
        ret_list.insert(0, "*")
        return ret_list
    
    @classmethod
    def load_json_data(cls, ncdu_data_file) -> FileInfo:
        """
        Loads data from json_file_path , created by 'ncdu -o ...' command
        Params:
            ncdu_data_file - path to the json file
        Return: a FileInfo object
        """
        count1 = time.perf_counter()
        ncdu_data = []
        lines_number = FileUtils.get_file_lines_number(ncdu_data_file)
        logger.info("Loading data from {} ... ".format(ncdu_data_file))
            
        with open(ncdu_data_file, "r", errors='ignore') as fh:
            ncdu_data = json.load(fh)        
        # first 3 elements has no importance for us
        files_data = ncdu_data[3]
        FileInfo.set_percent_batch_size(lines_number)
        root_file = FileInfo("", **files_data[0])
        root_file.add_children(files_data[1:])
        logger.info("Data loaded.")
        count2 = time.perf_counter()
        logger.debug("Time spent on loading data {}".format(dt.timedelta(seconds = round(count2 - count1))))
        return root_file
    
    @classmethod
    def parseConfigFile(cls, cfg_file) -> configparser.ConfigParser    :
        """
        Parse the config file and return it as dict.
        see https://docs.python.org/3.6/library/configparser.html#module-configparser
        Known sections from config file: FILE_MENU
        @param: cfg_file
        @return: ConfigParser
        """
        cfg = configparser.ConfigParser()
        cfg.optionxform=str
        cfg.read(cfg_file)
        return cfg

    @classmethod
    def ncdu_scan_folder(cls, folder_path : str, exclude_files: str, out_file: str) -> bool:
        """
        Scan the folder_path with ncdu command, exclude the exclude_files 
        and save the results to out_file
        @param: folder_path - folder to be scanned
        @param: exclude_files - filenames to be excluded
        @param: out_file - where to save the ncdu output , it is a json file
        @return: True ( on success), False (if somethinmg went wrong)
        """
        if os.path.isdir(folder_path) == False:
            logger.error("{} is not a dir, or it is not readable".format(folder_path))
            return False
        cmd = ['ncdu', '-e', '-x', '-o', out_file]
        if exclude_files:
            cmd.append('--exclude')
            cmd.append(exclude_files)
        cmd.append(folder_path)
        proc = subprocess.run(cmd, capture_output=False)        
        if proc.returncode == 0:
            return True
        else:
            logger.error("Command {} , exit code = {}, ERROR: {}".format(
                    proc.args, proc.returncode, proc.stderr))
            return False




class Window (tk.Frame):
    def __init__(self, master : tk.Tk, root_file: FileInfo , cfg_data: configparser.ConfigParser,
                 logger : logging.Logger):
        
        self.const_multiplier = 1.0/1024/1024 # to transform file size in MB        

        self.master = master       
        self.create_widgets(root_file, cfg_data)
        self.logger = logger
        master.wm_title(root_file.path)
        self.logger.debug("Used memory {:.2f} GB".format(
                psutil.Process(os.getpid()).memory_info().rss/1024/1024/1024)) 
        pass

    def create_widgets(self, root_file: FileInfo, cfg_data:configparser.ConfigParser) -> None:
        self.master.columnconfigure(0, weight=1)
        self.master.columnconfigure(1, weight=1000)
        self.master.columnconfigure(2, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.master.rowconfigure(1, weight=1000)
        self.root_file = root_file
        # combobox (a.k.a dropdown list) to show all files owners
        self.owners_label = tk.Label(self.master, text = "Username:")
        self.owners_label.grid(row=0, column=0)
        self.combo_owners = ttk.Combobox()
        self.combo_owners.grid(row=0, column=1, sticky="w")
        self.combo_owners['values'] = FileUtils.get_all_usernames()
        self.combo_owners.set(self.combo_owners['values'][0])
        self.combo_owners['state'] = 'readonly'
        self.combo_owners.bind("<<ComboboxSelected>>", self.onOwnerChange)
        # TreeView to show files structure
        columns = ("size", "per_user") 
        self.tree= ttk.Treeview(self.master, columns=columns ,height = 20, selectmode='browse')        
        self.tree.grid(row=1, column = 0, columnspan = 2, sticky='nsew')
        
 
        self.tree.heading('#0', text='Path')
        self.tree.heading('size', text="Size(MB)")
        self.tree.heading('per_user', text='Owned by *')
        # create scroll bar on treeview
        v_scrollbar = ttk.Scrollbar(self.master, command=self.tree.yview, orient='vertical')
        v_scrollbar.grid(row=1, column=2, sticky='ns')
        self.tree.configure(yscrollcommand=v_scrollbar.set)
        for c in columns:
            self.tree.column(c, anchor=tk.E)
        self.add_popup_menu_on_tree_view(cfg_data)
        
        self.root_file.set_selected_owner("*")        
        if self.root_file.name is None:
            return
        self.root_file.sort_children_by_size_group_by_selected_owner(0)
        self.populate_data(self.root_file, "")        
        self.selected_item = None

    def onOwnerChange(self, event: tk.Event):
        """
        Owner name changed callback.
        When the owner name is changed we should recalculate the usage per respective user.
        """
        rf = self.root_file
        rf.set_selected_owner(event.widget.get())
        rf.sort_children_by_size_group_by_selected_owner(0)
        # repopulate the files structure tree
        for c in self.tree.get_children():
            self.tree.delete(c)
        self.populate_data(rf, "")
        self.tree.heading('per_user', text='Owned by {}'.format(event.widget.get()))        
     
        
 
    def populate_data(self, file_node : FileInfo, parent_name : str):

        total_size = file_node.get_hierarchy_size()
        total_size_str = "{:.3f}".format(total_size * self.const_multiplier)
        user_size = file_node.get_hierarchy_size_by_owner(file_node.get_selected_owner())
        user_size_str = "{:.3f}".format( user_size * self.const_multiplier)
        self.tree.insert(parent_name, tk.END, iid = file_node.path, 
                text = file_node.name, values = [total_size_str, user_size_str])
        for c in file_node.children:
            self.populate_data(c, file_node.path)

    def exec_shell(self, cmd: str):            
        path = str(Path(self.selected_item))
        if os.path.isdir(path):
            dir_path = path
        else:
            dir_path = os.path.dirname(path)
        cmd = cmd.replace('$DIR', dir_path)
        cmd = cmd.replace('$FILE', path)
        self.logger.debug("Execute command '{}'".format(cmd))
        os.system(cmd)


    def add_popup_menu_on_tree_view(self, cfg_data: configparser.ConfigParser) -> None:
        self.popup_menu = tk.Menu(self.tree, tearoff=0)
        self.popup_menu.add_command(label="File Info", command = self.show_file_info)
        for option, cmd in cfg_data.items("FILE_MENU"):
            # dinamically define commands
            def item_cmd(cmd):
                def new_cmd():
                    self.exec_shell(cmd)
                return new_cmd
            i_cmd = item_cmd(cmd)
            self.popup_menu.add_command(label=option , command = i_cmd)

        self.tree.bind("<Button-3>", self.do_popup)

    
    
    def show_file_info(self):
        f_path = self.selected_item
        win = tk.Toplevel()
        win.wm_title("File Info - {}".format(f_path))
        info_list =["{}", "Size: {} B", "Modfied: {}", "Owner: {}"]
        info_str = "\n\t".join(info_list)
        
        size = os.path.getsize(f_path)
        m_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(f_path)))
        owner = Path(f_path).owner()

        info_str = info_str.format(f_path, size, m_time, owner)        
        f_info_text = tk.Text(win, bg="white")
        f_info_text.insert(1.0, info_str)
        f_info_text["state"] = "disabled"        
        f_info_text.configure(height=len(info_str.split("\n")))
        f_info_text.grid(row=1, column=1, padx=10, pady=10)
        
        # Button for closing
        close_button = tk.Button(win, text="Close", command=win.destroy)
        close_button.grid(row=3, column=1, padx=10, pady=10)
        
        win.columnconfigure(0, weight=3)
        win.columnconfigure(1, weight=10)
        win.columnconfigure(2, weight=3)
        win.rowconfigure(0, weight=1)
        win.rowconfigure(1, weight=10)
        win.rowconfigure(2, weight=1)


    def do_popup(self, event):
    # display the popup menu for tree view
        try:
            self.selected_item = self.tree.identify_row(event.y)            
            self.popup_menu.tk_popup(event.x_root, event.y_root)
        
        finally:
            # make sure to release the grab (Tk 8.0a1 only)
            self.popup_menu.grab_release()


if __name__  == "__main__":

    args = IOutil.readArgs()    
    logging.config.fileConfig(os.path.join( os.path.dirname(os.path.abspath(__file__)), 'log.ini'))
    logger = logging.getLogger('PYNCDU')
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    FileUtils.set_logger(logger)
    


    # for debugg printing
    my_pp = pprint.PrettyPrinter()

    root_file = FileInfo("")


    # load the config data file
    if args.config:
        cfg_file = args.config
    else:
        cfg_file = os.path.join( os.path.dirname(os.path.abspath(__file__)), 'cfg.ini')

    if os.path.isfile(cfg_file):
        cfg_data = FileUtils.parseConfigFile(cfg_file)
    else:
        cfg_data = configparser.ConfigParser()



    if args.load:      
       ncdu_data_file = args.load

    if args.scan:
        fp, ncdu_data_file = tempfile.mkstemp( 
                dir = tempfile.gettempdir(), prefix="ncdu_", suffix = ".json")        
        os.close(fp)
        logger.info("Scanning folder {} (exclude {}) ...".format(args.scan, args.exclude))
        if FileUtils.ncdu_scan_folder(args.scan, args.exclude, ncdu_data_file) == False:
            sys.exit(11)
        logger.info("End scanning {}".format(args.scan))

    
    root_file = FileUtils.load_json_data(ncdu_data_file)   
    
       

    top_tk = tk.Tk()
    window = Window(top_tk, root_file, cfg_data, logger)
    top_tk.mainloop()
    
    logging.shutdown()
