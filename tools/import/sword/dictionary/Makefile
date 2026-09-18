# Makefile for SWORD Dictionary to SQLite Converter

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -I../common
LDFLAGS = -L../common -lswordcommon -lsword -lsqlite3 -lzip

TARGET = sword2dictionary
SOURCE = sword2dictionary.cpp
COMMON_LIB = ../common/libswordcommon.a

.PHONY: all clean test inspect install-deps

all: $(TARGET)

$(COMMON_LIB):
	$(MAKE) -C ../common

$(TARGET): $(SOURCE) $(COMMON_LIB)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCE) $(LDFLAGS)

clean:
	rm -f $(TARGET) dictionary_*.db

test: $(TARGET)
	./convert-webster.sh

inspect:
	@if [ ! -f dictionary_webster.db ]; then \
		echo "Error: dictionary_webster.db not found. Run 'make test' first."; \
		exit 1; \
	fi
	@echo "=== Module Info ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('dictionary_webster.db'); c=db.cursor(); row=c.execute('SELECT * FROM module_info').fetchone(); print(f'  Abbreviation: {row[2]}\\n  Full Name: {row[3]}\\n  Type: {row[4]}\\n  Language: {row[6]}'); db.close()"
	@echo ""
	@echo "=== Entry Count ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('dictionary_webster.db'); c=db.cursor(); count=c.execute('SELECT COUNT(*) FROM dictionary_entry').fetchone()[0]; print(f'  Total entries: {count:,}'); db.close()"
	@echo ""
	@echo "=== Sample Entries ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('dictionary_webster.db'); c=db.cursor(); [print(f'  {row[0]}: {row[1][:60]}...') for row in c.execute('SELECT entry_key, definition FROM dictionary_entry LIMIT 5').fetchall()]; db.close()"

install-deps:
	sudo apt-get update
	sudo apt-get install -y build-essential libsword-dev libsqlite3-dev libzip-dev
