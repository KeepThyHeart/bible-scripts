# Makefile for SWORD Commentary to SQLite Converter

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -I../common
LDFLAGS = -L../common -lswordcommon -lsword -lsqlite3 -lzip -lz -l:libzstd.so.1

TARGET = sword2commentary
SOURCE = sword2commentary.cpp
COMMON_LIB = ../common/libswordcommon.a

.PHONY: all clean test inspect install-deps

all: $(TARGET)

$(COMMON_LIB):
	$(MAKE) -C ../common

$(TARGET): $(SOURCE) $(COMMON_LIB)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCE) $(LDFLAGS)

clean:
	rm -f $(TARGET) commentary_*.db

test: $(TARGET)
	./convert-wesley.sh

inspect:
	@if [ ! -f commentary_wesley.db ]; then \
		echo "Error: commentary_wesley.db not found. Run 'make test' first."; \
		exit 1; \
	fi
	@echo "=== Module Info ==="
	@sqlite3 commentary_wesley.db "SELECT * FROM module_info;"
	@echo ""
	@echo "=== Entry Count ==="
	@sqlite3 commentary_wesley.db "SELECT COUNT(*) as total_entries FROM commentary_entry;"
	@echo ""
	@echo "=== Entry Levels ==="
	@sqlite3 commentary_wesley.db "SELECT entry_level, COUNT(*) as count FROM commentary_entry GROUP BY entry_level;"
	@echo ""
	@echo "=== Sample Entry (Genesis 1:1) ==="
	@sqlite3 commentary_wesley.db "SELECT substr(content, 1, 200) || '...' FROM commentary_entry WHERE verse_id_start = 1001001 LIMIT 1;"

install-deps:
	sudo apt-get update
	sudo apt-get install -y build-essential libsword-dev libsqlite3-dev libzip-dev zlib1g-dev
