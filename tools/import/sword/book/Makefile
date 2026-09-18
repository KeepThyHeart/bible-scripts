# Makefile for SWORD Book to SQLite Converter

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -I../common
LDFLAGS = -L../common -lswordcommon -lsword -lsqlite3 -lzip

TARGET = sword2book
SOURCE = sword2book.cpp
COMMON_LIB = ../common/libswordcommon.a

.PHONY: all clean test inspect install-deps

all: $(TARGET)

$(COMMON_LIB):
	$(MAKE) -C ../common

$(TARGET): $(SOURCE) $(COMMON_LIB)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCE) $(LDFLAGS)

clean:
	rm -f $(TARGET) book_*.db

test: $(TARGET)
	@if [ ! -f ../test/data/Finney.zip ]; then \
		echo "Error: Test file ../test/data/Finney.zip not found."; \
		exit 1; \
	fi
	./$(TARGET) --input ../test/data/Finney.zip --output book_finney.db

inspect:
	@if [ ! -f book_finney.db ]; then \
		echo "Error: book_finney.db not found. Run 'make test' first."; \
		exit 1; \
	fi
	@echo "=== Module Info ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('book_finney.db'); c=db.cursor(); row=c.execute('SELECT * FROM module_info').fetchone(); print(f'  Abbreviation: {row[2]}\\n  Full Name: {row[3]}\\n  Author: {row[4]}\\n  Language: {row[8]}'); db.close()"
	@echo ""
	@echo "=== Section Count ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('book_finney.db'); c=db.cursor(); count=c.execute('SELECT COUNT(*) FROM book_section').fetchone()[0]; print(f'  Total sections: {count:,}'); db.close()"
	@echo ""
	@echo "=== Table of Contents (Top Level) ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('book_finney.db'); c=db.cursor(); [print(f'  {row[0]}. {row[1]}') for row in c.execute('SELECT section_number, title FROM book_section WHERE parent_section_id IS NULL ORDER BY section_number LIMIT 10').fetchall()]; db.close()"
	@echo ""
	@echo "=== Sample Section Content ==="
	@python3 -c "import sqlite3; db=sqlite3.connect('book_finney.db'); c=db.cursor(); row=c.execute('SELECT title, content FROM book_section WHERE content IS NOT NULL LIMIT 1').fetchone(); print(f'  Title: {row[0]}\\n  Content: {row[1][:200]}...') if row else print('  No content found'); db.close()"

install-deps:
	sudo apt-get update
	sudo apt-get install -y build-essential libsword-dev libsqlite3-dev libzip-dev
