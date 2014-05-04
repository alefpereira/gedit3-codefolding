from gi.repository import GObject, Gio, Gtk, Gedit, Gdk, GtkSource
import re, inspect, os, sys, gettext
lang_support = {
	'C':{'startPattern':'/\*\*(?!\*)|^(?![^{]*?//|[^{]*?/\*(?!.*?\*/.*?\{)).*?\{\s*($|//|/\*(?!.*?\*/.*\S))','stopPattern':'(?<!\*)\*\*/|^\s*\}'},
	'C++':{'startPattern':'/\*\*(?!\*)|^(?![^{]*?//|[^{]*?/\*(?!.*?\*/.*?\{)).*?\{\s*($|//|/\*(?!.*?\*/.*\S))','stopPattern':'(?<!\*)\*\*/|^\s*\}'},
	'CSS':{'startPattern':'/\*\*(?!\*)|\{\s*($|/\*(?!.*?\*/.*\S))|\/\*\s*@group\s*.*\s*\*\/','stopPattern':'(?<!\*)\*\*/|^\s*\}|\/*\s*@end\s*\*\/|\}$'},
	'Graphviz Dot':{'startPattern':'\{','stopPattern':'\}'},
	'Java':{'startPattern':'(\{\s*(//.*)?$|^\s*// \{\{\{)','stopPattern':'^\s*(\}|// \}\}\}$)'},
	'JavaScript':{'startPattern':'\{\s*(//.*)?$|\[\s*(//.*)?$|\(\s*(//.*)?$','stopPattern':'^\s*\}|^\s*\]|^\s*\)'},
	'JSON':{'startPattern':'(^\s*[{\[](?!.*[}\]],?\s*$)|[{\[]\s*$)','stopPattern':'(^\s*[}\]])'},
	'Lua':{'startPattern':'\\b(function|local\s+function|then|do|repeat)\\b|{[ \t]*$|\[\[','stopPattern':'\\bend\\b|^\s*}|\]\]'},
	'Objective-C':{'startPattern':('/\*\*(?!\*)'
																 '|^(?![^{]*?//|[^{]*?/\*(?!.*?\*/.*?\{)).*?\{\s*($|//|/\*(?!.*?\*/.*\S))'
																 '|^@(interface|protocol|implementation)\\b (?!.*;)'),'stopPattern':'(?<!\*)\*\*/|^\s*\}|^@end\\b'},
	'Perl':{'startPattern':'(/\*|(\{|\[|\()\s*$)','stopPattern':'(\*/|^\s*(\}|\]|\)))'},
	'Prolog':{'startPattern':'%\s*region \w*','stopPattern':'%\s*end(\s*region)?'},
	'R':{'startPattern':'^[^#]*(\([^\)]*$|\{\s*$)','stopPattern':'(^\s*\)|^\s*\})'},
	'Ruby':{'startPattern':('^(\s*(module|class|def(?!.*\\bend\s*$)|unless|if|case|begin|for|while|until|^=begin|("(\\.|[^"])*"|'
													'\'(\\.|[^\'])*\'|[^#"\'])*(\s(do|begin|case)|(?<!\$)[-+=&|*/~%^<>~]\s*(if|unless)))\\b(?![^;]*;.*?\\bend\\b)|'
													'("(\\.|[^"])*"|\'(\\.|[^\'])*\'|[^#"\'])*(\{(?![^}]*\})|\[(?![^\]]*\]))).*$'),
					'stopPattern':'((^|;)\s*end\s*([#].*)?$|(^|;)\s*end\\..*$|^\s*[}\]],?\s*([#].*)?$|^=end)'},
	'sh':{'startPattern':'\\b(if|case)\\b|(\{|\\b(do)\\b)$','stopPattern':'^\s*(\}|\\b(done|fi|esac)\\b)'},
	'Scheme':{'startPattern':'^ [ \t]* \((?<par>( [^()\n]++ | \( \g<par> \)? )*+$)','stopPattern':'^\s*$'},
	'SQL':{'startPattern':'\s*\(\s*$','stopPattern':'^\s*\)'},
	'XML':{'startPattern':'^\s*(<[^!?%/](?!.+?(/>|</.+?>))|<[!%]--(?!.+?--%?>)|<%[!]?(?!.+?%>))','stopPattern':'^\s*(</[^>]+>|[/%]>|-->)\s*$'}
}

class CodeFoldingAppAct (GObject.Object, Gedit.AppActivatable):

	app = GObject.property(type=Gedit.App)

	def __init__(self):
		GObject.Object.__init__(self)

	def do_activate(self):
		self.app.add_accelerator("<Alt><Shift>T", "win.ToggleAll", None)
		self.app.add_accelerator("<Alt><Shift>C", "win.FoldCurrent", None)
		self.menu_ext = self.extend_menu("view-section")

		_ = lambda s: gettext.dgettext('codefolding', s)

		item = Gio.MenuItem.new(_("Toggle all blocks"), "win.ToggleAll")
		self.menu_ext.prepend_menu_item(item)
		item = Gio.MenuItem.new(_("Fold Current Block"), "win.FoldCurrent")
		self.menu_ext.prepend_menu_item(item)

	def do_deactivate(self):
		self.app.remove_accelerator("win.ToggleAll", None)
		self.app.remove_accelerator("win.FoldCurrent", None)
		self.menu_ext = None

################################################################
# Window activatable extension
################################################################
class CodeFoldingWinAct(GObject.Object, Gedit.WindowActivatable):

	window = GObject.property(type=Gedit.Window)

	def __init__(self):
		GObject.Object.__init__(self)

	def do_activate(self):
		self.worker = CodeFolder(self.window)
		self.event_id = self.window.connect('active-tab-changed',self.worker.handle_tab_activated)

		self.action_toggle_all = Gio.SimpleAction(name="ToggleAll")
		self.action_toggle_all.connect('activate',
				lambda a, p: self.worker.on_toggle_all())
		self.window.add_action(self.action_toggle_all)

		self.action_fold_current = Gio.SimpleAction(name="FoldCurrent")
		self.action_fold_current.connect('activate',
				lambda a, p: self.worker.fold_current_block())
		self.window.add_action(self.action_fold_current)

	def do_deactivate(self):
		self.window.disconnect(self.event_id)
		self.window.remove_action("ToggleAll")
		self.window.remove_action("FoldCurrent")
		self.action_toggle_all = None
		self.action_fold_current = None
		self.worker.clean_up()
		self.worker = None

	def do_update_state(self):
		_can_activate = False
		if self.window.get_active_document() != None:
			_lang = self.window.get_active_document().get_language()
			_can_activate = _lang != None and _lang.get_name() in lang_support.keys()
		self.action_toggle_all.set_enabled(_can_activate)
		self.action_fold_current.set_enabled(_can_activate)
################################################################
# Main code folding worker
################################################################	
class CodeFolder(GObject.Object):
	def __init__(self,window):
		self.window = window
		self.tab_event_handlers = {}

	def on_toggle_all(self):
		_doc = self.get_current_document()
		_num_lines = _doc.get_line_count()
		_i = 0
		_inf_o = self._info_for_line_at(_i)
		while _inf_o['blockstart'] != True:
			_i += 1
			_inf_o = self._info_for_line_at(_i)
		while _i < _num_lines:
			_i = self.toggle_at_line(_i)+1
	def _info_for_line_at(self,i):
		_doc = self.get_current_document()
		_lang = _doc.get_language().get_name()
		_startPattern = lang_support[_lang]['startPattern']
		_stopPattern = lang_support[_lang]['stopPattern']
		_inf_o = {'blockstart':False,'blockend':False,'regular':False,'indent':-1}
		_its_r = _doc.get_iter_at_line(i)
		if _its_r.ends_line():
			_inf_o['regular'] = True
			return _inf_o 
		_ite_r = _its_r.copy()
		_ite_r.forward_to_line_end()
		_lin_e = _doc.get_text(_its_r,_ite_r,False).strip()

		if re.search(_startPattern,_lin_e) != None:
			_inf_o['blockstart'] = True
		if re.search(_stopPattern,_lin_e) != None:
			_inf_o['blockend'] = True
		if _inf_o['blockstart'] != True and _inf_o['blockend'] != True:
			_inf_o['regular'] = True
			_inf_o['blockstart'] = False
			_inf_o['blockend'] = False
		if _inf_o['blockstart'] == True and _inf_o['blockend'] == True:
			_inf_o['regular'] = True
			_inf_o['blockstart'] = False
			_inf_o['blockend'] = False
		return _inf_o
	def toggle_at_line(self,num,recursive=False):
		_doc = self.get_current_document()
		its_r = _doc.get_iter_at_line(num)
		_tt = _doc.get_tag_table()
		_tag = _tt.lookup('blockfold')
		if _tag == None:
			_tag = _doc.create_tag('blockfold', invisible=True)
		_level = 0
		while its_r.is_end() != True:
			_num = its_r.get_line()
			_inf_o = self._info_for_line_at(_num)
			if _inf_o['blockstart'] == True:
				_level += 1
			if _inf_o['blockend'] == True:
				_level -= 1
			if _level == 0:
				_ite_r = _doc.get_iter_at_line(num)
				_ite_r.forward_line()
				if _ite_r.has_tag(_tag):
					_doc.remove_tag(_tag,_ite_r,its_r)
				else:
					_doc.apply_tag(_tag,_ite_r,its_r)
				break
			its_r.forward_line()
		return its_r.get_line()
	def toggle_current_line(self):
		_doc = self.get_current_document()
		_ite_r = _doc.get_iter_at_mark(_doc.get_insert())
		self.toggle_at_line(_ite_r.get_line())
	def handle_tab_activated(self,win,tab):
		# Now the signals can be treated.
		# If already have a entry on dictionary for this tab, means that
		# we already have signal for this tab, so we do nothing.
		# In case of there is no entry for this means we have to create (by now, only once).
		try:
			if self.tab_event_handlers[tab.get_document()]:
				pass
		except KeyError:
			# Giving the tab directly as argument deal with case of files loaded during Gedit startup
			# For instance, using Restore Tab Plugin or opening a file by double-clicking it.
			# As result, no need to self.cur_tab = tab, once it is not a safe method to do this.
			self.tab_event_handlers[tab.get_document()] = tab.get_document().connect('loaded',self.handle_doc_load, tab)
	def insert_expander(self,doc,tab):
		_doc = doc
		_view = tab.get_view()
		_gutter = _view.get_gutter(Gtk.TextWindowType.LEFT)
		_r = FoldingIndicatorRenderer()
		_r.set_worker(self)
		_r.set_visible(True)
		_r.set_size(10)
		_gutter.insert(_r,-10) #leave space for LINES and MARKS renderers
	def handle_doc_load(self,doc,err,tab):
		if doc.get_language() and doc.get_language().get_name() in lang_support.keys():
			self.insert_expander(doc, tab)
	def clean_up(self):
		for tab in self.tab_event_handlers:
			tab.disconnect(self.tab_event_handlers[tab])
		self.tab_event_handlers.clear()

	def get_current_document(self):
		return self.window.get_active_document()
	def get_leading_ws(self,s):
		_view = self.cur_tab.get_view()
		_iw = _view.get_property('tab-width')
		return s.count('\t')*_iw+s.count('\s')
	def fold_current_block(self):
		_bool = 0
		_doc = self.get_current_document()
		_iter = _doc.get_iter_at_mark(_doc.get_insert())
		_info = self._info_for_line_at(_iter.get_line())
		if _info['blockstart'] == True:
			self.toggle_at_line(_iter.get_line())
		else:
			while True:
				_iter.backward_line()
				_info = self._info_for_line_at(_iter.get_line())
				if _info['blockend'] == True:
					_bool = 1
				if _info['blockstart'] == True:
					if _bool == 0:
						break
					else:
						_bool = 0
			self.toggle_at_line(_iter.get_line())
			_doc.place_cursor(_iter)
################################################################
# Renderer for the folding indicators
################################################################
class FoldingIndicatorRenderer(GtkSource.GutterRenderer):
	def set_worker(self,w):
		self.worker = w
	def do_draw(self,cr,bk,cell,st,en,state):
		_num = st.get_line()
		_inf_o = self.worker._info_for_line_at(_num)
		if _inf_o['blockstart'] == True:
			_tag = self.worker.get_current_document().get_tag_table().lookup('blockfold')
			st.forward_line()
			_iter = self.worker.get_current_document().get_iter_at_line(st.get_line())
			if _tag != None and _iter.has_tag(_tag) == True:
				st.backward_line()
				cr.set_source_rgb(0, 0, 0.8)
				cr.set_line_width(0.5)
				_cy = cell.y+cell.height/2
				_cx = cell.x+cell.width/2
				cr.rectangle(_cx-5,_cy-5,10,10)
				cr.stroke()
				cr.move_to(_cx,_cy-5)
				cr.line_to(_cx,_cy+5)
				cr.move_to(_cx-5,_cy)
				cr.line_to(_cx+5,_cy)
				cr.stroke()
			elif _tag == None or _iter.has_tag(_tag) == False:
				st.backward_line()
				cr.set_source_rgb(0, 0, 0.8)
				cr.set_line_width(0.5)
				_cy = cell.y+cell.height/2
				_cx = cell.x+cell.width/2
				cr.rectangle(_cx-5,_cy-5,10,10)
				cr.stroke()
				cr.move_to(_cx-5,_cy)
				cr.line_to(_cx+5,_cy)
				cr.stroke()
		elif _inf_o['regular'] == True:
			cr.set_source_rgb(0, 0, 0.8)
			cr.set_line_width(0.5)
			_cx = cell.x+cell.width/2
			cr.move_to(_cx,cell.y)
			cr.line_to(_cx,cell.y+cell.height)
			cr.stroke()
		elif _inf_o['blockend'] == True:
			cr.set_source_rgb(0, 0, 0)
			cr.set_line_width(0.5)
			_cx = cell.x+cell.width/2
			_cy = cell.y+cell.height/2
			cr.move_to(_cx,cell.y)
			cr.line_to(_cx,_cy+5)
			cr.line_to(_cx+cell.width/2,_cy+5)
			cr.stroke()
	def do_query_activatable(self,iter,area,event):
		_info = self.worker._info_for_line_at(iter.get_line())
		if _info['blockstart'] == True:
			return True
		return False
	def do_activate(self,iter,area,event):
		if event.get_button()[1] == 1:
			self.worker.toggle_at_line(iter.get_line())
