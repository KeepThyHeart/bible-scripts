# Makefile for SWORD Devotional to SQLite Converter

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -I../common
LDFLAGS = -L../common -lswordcommon -lsword -lsqlite3 -lzip -lz -l:libzstd.so.1

TARGET = sword2devotional
SOURCE = sword2devotional.cpp
COMMON_LIB = ../common/libswordcommon.a

.PHONY: all clean test inspect install-deps

all: $(TARGET)

$(COMMON_LIB):
	$(MAKE) -C ../common

$(TARGET): $(SOURCE) $(COMMON_LIB)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCE) $(LDFLAGS)

clean:
	rm -f $(TARGET) devotional_*.db

test: $(TARGET)
	@if [ ! -f ../test/data/SME.zip ]; then \
		echo "Error: Test file ../test/data/SME.zip not found."; \
		exit 1; \
	fi
	./$(TARGET) --input ../test/data/SME.zip --output devotional_spurgeon_morning.db

inspect:
	@if [ ! -f devotional_spurgeon_morning.db ]; then \
		echo "Error: devotional_spurgeon_morning.db not found. Run 'make test' first."; \
		exit 1; \
	fi
	@echo "=== Module Info ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('devotional_spurgeon_morning.db'); c=db.cursor(); row=c.execute('SELECT * FROM module_info').fetchone(); print(f'  Abbreviation: {row[2]}\\n  Full Name: {row[3]}\\n  Type: {row[9]}\\n  Total Days: {row[10]}\\n  Language: {row[8]}'); db.close()"
	@echo ""
	@echo "=== Entry Count ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('devotional_spurgeon_morning.db'); c=db.cursor(); count=c.execute('SELECT COUNT(*) FROM devotional_entry').fetchone()[0]; print(f'  Total entries: {count:,}'); db.close()"
	@echo ""
	@echo "=== Sample Entries ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('devotional_spurgeon_morning.db'); c=db.cursor(); [print(f'  Day {row[0]}: {row[1]} - {row[3][:60]}...') for row in c.execute('SELECT day_number, date_label, title, content FROM devotional_entry LIMIT 5').fetchall()]; db.close()"

install-deps:
	sudo apt-get update
	sudo apt-get install -y build-essential libsword-dev libsqlite3-dev libzip-dev zlib1g-dev
