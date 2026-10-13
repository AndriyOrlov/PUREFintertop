CREATE TABLE images (width INTEGER,checksum TEXT,format TEXT,source TEXT,height INTEGER,data BLOB,source_type INTEGER,origin TEXT,id INTEGER PRIMARY KEY);

CREATE TABLE items_groups (lock_mode INTEGER,background_color TEXT,id INTEGER PRIMARY KEY);

CREATE TABLE items_drawings (strokes BLOB,id INTEGER PRIMARY KEY);

CREATE TABLE items_notes (fixed_size TEXT,background_color TEXT,text_color TEXT,style INTEGER,text TEXT,id INTEGER PRIMARY KEY);

CREATE TABLE items_images (playback_speed REAL,playback_frame INTEGER,playback_state INTEGER,image_bounds BLOB,image_transform BLOB,flags INTEGER,image INTEGER,id INTEGER PRIMARY KEY);

CREATE TABLE items (comment INTEGER,opacity REAL,z REAL,locked INTEGER,parent INTEGER,sort_order BLOB,transform BLOB,name TEXT,id INTEGER PRIMARY KEY);

CREATE TABLE metadata (thumbnail BLOB,saved INTEGER,last_load_checksum TEXT,last_load_path TEXT,last_save_path TEXT,vertical_scroll INTEGER,scene_rect TEXT,view_transform TEXT,application_version TEXT,horizontal_scroll INTEGER,id INTEGER PRIMARY KEY);
