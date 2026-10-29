// Sequential, read-only SQLite -> query-bound uint32 postings. Linux C++17.
// Stable public SQLite C ABI declarations avoid a development-header dependency.
// No private SQLite APIs, extensions, network access or source database writes.
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
extern "C" {
struct sqlite3; struct sqlite3_stmt;
int sqlite3_open_v2(const char*, sqlite3**, int, const char*);
int sqlite3_close(sqlite3*);
const char* sqlite3_errmsg(sqlite3*);
int sqlite3_prepare_v2(sqlite3*,const char*,int,sqlite3_stmt**,const char**);
int sqlite3_step(sqlite3_stmt*);
int sqlite3_finalize(sqlite3_stmt*);
long long sqlite3_column_int64(sqlite3_stmt*,int);
double sqlite3_column_double(sqlite3_stmt*,int);
const unsigned char* sqlite3_column_text(sqlite3_stmt*,int);
int sqlite3_column_bytes(sqlite3_stmt*,int);
int sqlite3_exec(sqlite3*,const char*,int(*)(void*,int,char**,char**),void*,char**);
const char* sqlite3_libversion();
}
struct File {
    int fd=-1; void* data=nullptr; size_t size=0;
    File(const std::string& path, size_t bytes):size(bytes) {
        fd=open(path.c_str(),O_RDWR|O_CREAT|O_TRUNC,0644);
        if(fd<0 || ftruncate(fd,bytes)) throw std::runtime_error("Cannot allocate projection file: "+path);
        if(bytes) {
            data=mmap(nullptr,bytes,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
            if(data==MAP_FAILED) {data=nullptr; throw std::runtime_error("mmap failed: "+path);}
        }
    }
    ~File(){if(data)munmap(data,size);if(fd>=0)close(fd);}
    void flush(){if(size && msync(data,size,MS_SYNC)) throw std::runtime_error("Projection flush failed");}
};
struct Database {
    sqlite3* db=nullptr;
    explicit Database(const char* uri) {
        if(sqlite3_open_v2(uri,&db,1|0x40|0x8000,nullptr)) throw std::runtime_error("Cannot open source SQLite read-only");
        if(sqlite3_exec(db,"PRAGMA cache_size=-65536; PRAGMA temp_store=MEMORY;",nullptr,nullptr,nullptr))
            throw std::runtime_error(sqlite3_errmsg(db));
    }
    ~Database(){if(db)sqlite3_close(db);}
};
struct Statement {
    sqlite3_stmt* stmt=nullptr; sqlite3* db;
    Statement(sqlite3* d,const char* sql):db(d) {
        if(sqlite3_prepare_v2(db,sql,-1,&stmt,nullptr)) throw std::runtime_error(sqlite3_errmsg(db));
    }
    ~Statement(){if(stmt)sqlite3_finalize(stmt);}
    bool next(){int result=sqlite3_step(stmt);if(result==100)return true;if(result==101)return false;throw std::runtime_error(sqlite3_errmsg(db));}
};
int main(int argc,char** argv) {
    try {
        if(argc!=5) throw std::runtime_error("Usage: helper SQLITE_URI WANTED_TSV OUTPUT_DIR CHUNKS");
        uint64_t n=std::stoull(argv[4]);
        if(n>std::numeric_limits<uint32_t>::max()) throw std::runtime_error("Projection requires <=2^32-1 chunks");
        std::ifstream wanted(argv[2]);
        if(!wanted) throw std::runtime_error("Cannot open wanted terms");
        std::vector<uint64_t> starts, ends, positions;
        std::vector<int> slots;
        uint64_t tid,df,total=0;
        while(wanted>>tid>>df) {
            if(tid>100000000 || !df || df>n) throw std::runtime_error("Invalid selected term statistics");
            if(slots.size()<=tid) slots.resize(tid+1,-1);
            if(slots[tid]!=-1) throw std::runtime_error("Duplicate selected term");
            slots[tid]=static_cast<int>(starts.size());
            starts.push_back(total);positions.push_back(total);total+=df;ends.push_back(total);
        }
        if(!wanted.eof()) throw std::runtime_error("Malformed wanted term list");
        std::string root=argv[3];
        File ids(root+"/cids.u32",total*sizeof(uint32_t)), tf(root+"/tf.u32",total*sizeof(uint32_t));
        File lengths(root+"/lengths.f64",n*sizeof(double)), norms(root+"/norms.f64",n*sizeof(double));
        File keys(root+"/chunk_ids.s64",n*64);
        auto* id_data=static_cast<uint32_t*>(ids.data);auto* tf_data=static_cast<uint32_t*>(tf.data);
        Database source(argv[1]);
        uint64_t count=0;
        {
            Statement rows(source.db,"SELECT cid,length,norm,chunk_id FROM chunks ORDER BY cid");
            while(rows.next()) {
                auto cid=sqlite3_column_int64(rows.stmt,0);
                if(cid!=static_cast<long long>(count) || count>=n) throw std::runtime_error("Noncontiguous chunk IDs");
                static_cast<double*>(lengths.data)[count]=sqlite3_column_double(rows.stmt,1);
                static_cast<double*>(norms.data)[count]=sqlite3_column_double(rows.stmt,2);
                auto key=sqlite3_column_text(rows.stmt,3);int bytes=sqlite3_column_bytes(rows.stmt,3);
                if(!key || bytes<1 || bytes>64) throw std::runtime_error("Stable chunk ID needs 1..64 UTF-8 bytes");
                std::memcpy(static_cast<char*>(keys.data)+count*64,key,bytes);
                ++count;
            }
        }
        if(count!=n) throw std::runtime_error("Chunk geometry differs");
        uint64_t scanned=0,selected=0;
        {
            // The PRIMARY KEY(cid,tid) table contains tf. Never perform the
            // non-covering postings_term -> primary-row random lookup per hit.
            Statement rows(source.db,"SELECT cid,tid,tf FROM postings ORDER BY cid,tid");
            while(rows.next()) {
                auto cid=sqlite3_column_int64(rows.stmt,0), term=sqlite3_column_int64(rows.stmt,1);
                auto freq=sqlite3_column_int64(rows.stmt,2);
                if(cid<0 || static_cast<uint64_t>(cid)>=n || term<0 || freq<=0 ||
                   static_cast<uint64_t>(freq)>std::numeric_limits<uint32_t>::max()) throw std::runtime_error("Invalid source posting");
                if(static_cast<uint64_t>(term)<slots.size() && slots[term]>=0) {
                    int slot=slots[term];uint64_t at=positions[slot]++;
                    if(at>=ends[slot]) throw std::runtime_error("Selected DF overflow");
                    id_data[at]=static_cast<uint32_t>(cid);tf_data[at]=static_cast<uint32_t>(freq);++selected;
                }
                ++scanned;
                if(scanned%50000000==0) std::cout<<"{\"scanned_postings\":"<<scanned<<",\"selected\":"<<selected<<"}"<<std::endl;
            }
        }
        if(positions!=ends || selected!=total) throw std::runtime_error("Selected term DF differs from actual postings");
        ids.flush();tf.flush();lengths.flush();norms.flush();keys.flush();
        std::cout<<"{\"state\":\"complete\",\"scanned_postings\":"<<scanned<<",\"selected_postings\":"<<total
                 <<",\"sqlite\":\""<<sqlite3_libversion()<<"\"}"<<std::endl;
        return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<std::endl;return 1;}
}
