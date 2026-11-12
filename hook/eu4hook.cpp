// eu4hook.so: LD_PRELOAD hook for EU4 1.37.5 (Linux). See docs/re/spec.md for every address/layout claim.
// A socket thread accepts JSON-line requests; they run on the game's main thread from the interposed
// SteamAPI_RunCallbacks (first call in CApplication::UpdateOneFrame). Symbols are resolved by name.
#include <time.h>
#include <dlfcn.h>
#include <setjmp.h>
#include <signal.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <string>
#include <thread>
#include <vector>

#define HOOK_VERSION "0.2"

// ---------- tiny JSON (flat objects: string / number / bool / null / array of those) ----------
struct JVal {
    enum T { NUL, BOOL, NUM, STR, ARR } t = NUL;
    bool b = false;
    double n = 0;
    std::string s;
    std::vector<JVal> a;
};
using JObj = std::map<std::string, JVal>;

static void skipws(const char*& p) { while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++; }
static bool parse_str(const char*& p, std::string& out) {
    if (*p != '"') return false;
    p++;
    while (*p && *p != '"') {
        if (*p == '\\' && p[1]) {
            p++;
            switch (*p) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'u': {  // \uXXXX -> latin1 byte when < 256, else '?'
                    unsigned v = 0;
                    if (strlen(p) >= 5) { sscanf(p + 1, "%4x", &v); p += 4; }
                    out += v < 256 ? (char)v : '?';
                    break;
                }
                default: out += *p;
            }
            p++;
        } else out += *p++;
    }
    if (*p != '"') return false;
    p++;
    return true;
}
static bool parse_val(const char*& p, JVal& v, int depth = 0) {
    skipws(p);
    if (*p == '"') { v.t = JVal::STR; return parse_str(p, v.s); }
    if (*p == '[' && depth == 0) {
        v.t = JVal::ARR; p++; skipws(p);
        if (*p == ']') { p++; return true; }
        for (;;) {
            JVal e;
            if (!parse_val(p, e, 1)) return false;
            v.a.push_back(e); skipws(p);
            if (*p == ',') { p++; continue; }
            if (*p == ']') { p++; return true; }
            return false;
        }
    }
    if (!strncmp(p, "true", 4)) { v.t = JVal::BOOL; v.b = true; p += 4; return true; }
    if (!strncmp(p, "false", 5)) { v.t = JVal::BOOL; v.b = false; p += 5; return true; }
    if (!strncmp(p, "null", 4)) { v.t = JVal::NUL; p += 4; return true; }
    char* e;
    v.n = strtod(p, &e);
    if (e == p) return false;
    v.t = JVal::NUM; p = e;
    return true;
}
static bool parse_obj(const char* p, JObj& o) {
    skipws(p);
    if (*p != '{') return false;
    p++; skipws(p);
    if (*p == '}') return true;
    for (;;) {
        skipws(p);
        std::string k;
        if (!parse_str(p, k)) return false;
        skipws(p);
        if (*p != ':') return false;
        p++;
        JVal v;
        if (!parse_val(p, v)) return false;
        o[k] = v; skipws(p);
        if (*p == ',') { p++; continue; }
        return *p == '}';
    }
}
static std::string jesc(const std::string& s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c == '\n') o += "\\n";
        else if (c == '\t') o += "\\t";
        else if (c < 0x20 || c >= 0x80) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }  // game text is latin1
        else o += c;
    }
    return o + "\"";
}
static std::string jstr(const JObj& o, const char* k, const char* def = "") {
    auto it = o.find(k);
    return it != o.end() && it->second.t == JVal::STR ? it->second.s : def;
}
static double jnum(const JObj& o, const char* k, double def = 0) {
    auto it = o.find(k);
    return it != o.end() && it->second.t == JVal::NUM ? it->second.n : def;
}

// ---------- game symbols ----------
template <class T> static T sym(const char* name) {
    void* p = dlsym(RTLD_DEFAULT, name);
    if (!p) fprintf(stderr, "[eu4hook] MISSING SYMBOL %s\n", name);
    return (T)p;
}
struct SResult { bool ok; std::string msg; };  // CConsoleCmd::SResult, 0x28 bytes
static_assert(sizeof(SResult) == 0x28, "SResult layout");
static_assert(sizeof(std::string) == 0x20, "cxx11 string layout");

static struct Game {
    void** gs_inst;        // CCurrentGameState::_pInstance
    void** conmgr_inst;    // CConsoleCmdManager::_pInstance
    void (*conmgr_create)();
    void (*con_execute)(SResult*, void*, std::vector<std::string>*);
    uint64_t (*get_player)(void*);
    void (*date_str)(std::string*, const int*);
    void* update_one_frame;
    bool (*is_main_thread)();
    bool (*post)(void* session, void* cmd);
    void* (*get_session)(void* idler);
    void (*inc_speed_ctor)(void*);
    void* (*access_gamespeed)(void* idler);
    bool (*is_paused)(void* gamespeed);
    char* ingame_idler_vt;  // vtable for CInGameIdler
    bool ok;
} G;

static void resolve() {
    G.gs_inst = sym<void**>("_ZN17CCurrentGameState10_pInstanceE");
    G.conmgr_inst = sym<void**>("_ZN18CConsoleCmdManager10_pInstanceE");
    G.conmgr_create = sym<void (*)()>("_ZN18CConsoleCmdManager14CreateInstanceEv");
    G.con_execute = sym<decltype(G.con_execute)>("_ZN18CConsoleCmdManager7ExecuteERK6CArrayI7CStringE");
    G.get_player = sym<decltype(G.get_player)>("_ZNK10CGameState9GetPlayerEv");
    G.date_str = sym<decltype(G.date_str)>("_ZNK8CEU3Date12GetEu3StringEv");
    G.update_one_frame = sym<void*>("_ZN12CApplication14UpdateOneFrameEb");
    G.is_main_thread = sym<bool (*)()>("ThreadIsMainThread");
    G.post = sym<decltype(G.post)>("_ZN8CSession4PostEP8CCommand");
    G.get_session = sym<decltype(G.get_session)>("_ZNK12CInGameIdler10GetSessionEv");
    G.inc_speed_ctor = sym<void (*)(void*)>("_ZN25CIncreaseGameSpeedCommandC1Ev");
    G.access_gamespeed = sym<decltype(G.access_gamespeed)>("_ZN12CInGameIdler15AccessGameSpeedEv");
    G.is_paused = sym<decltype(G.is_paused)>("_ZNK10CGameSpeed16IsActuallyPausedEv");
    G.ingame_idler_vt = sym<char*>("_ZTV12CInGameIdler");
    G.ok = G.ingame_idler_vt && G.gs_inst && G.conmgr_inst && G.con_execute && G.get_player && G.date_str && G.update_one_frame &&
           G.is_main_thread && G.post && G.get_session;
}

// spec.md offsets (1.37.5)
static const int GS_DATE = 0x1da0, GS_IDLER = 0x1dd0, GS_SPEED = 0x1dd8;
static char* gs() { return G.gs_inst ? (char*)*G.gs_inst : nullptr; }
// Only an in-game idler counts: at the main menu / lobby the slot holds another idler class (calling
// in-game methods on it crashed the game), so compare the vptr with CInGameIdler's vtable.
static char* idler() {
    char* g = gs();
    char* i = g ? *(char**)(g + GS_IDLER) : nullptr;
    return i && *(char**)i == G.ingame_idler_vt + 16 ? i : nullptr;
}
static void* session() { char* i = idler(); return i ? G.get_session(i) : nullptr; }
static std::string date_now() {
    char* g = gs();
    if (!g) return "";
    std::string s;
    G.date_str(&s, (const int*)(g + GS_DATE));  // sret: constructs into s' storage (s is empty SSO, nothing leaks)
    return s;
}
static bool parse_date(const std::string& s, int d[3]) { return sscanf(s.c_str(), "%d.%d.%d", &d[0], &d[1], &d[2]) == 3; }
static long date_key(const std::string& s) { int d[3]; return parse_date(s, d) ? d[0] * 10000L + d[1] * 100 + d[2] : -1; }
static std::string player_tag() {
    char* g = gs();
    if (!g) return "";
    uint64_t t = G.get_player(g);
    char b[5] = {0};
    memcpy(b, &t, 4);
    return b;
}
static int paused() {
    char* i = idler();
    if (!i || !G.access_gamespeed || !G.is_paused) return -1;
    void* sp = G.access_gamespeed(i);
    return sp ? (G.is_paused(sp) ? 1 : 0) : -1;
}

// ---------- request plumbing ----------
struct Req {
    JObj o;
    std::string id_json = "null";
    std::string reply;
    bool done = false;
    // deferred "wait" state
    bool waiting = false;
    long until_key = 0;
    std::chrono::steady_clock::time_point deadline;
    bool stop_on_pause = true;
    bool pause_at_end = false;
    std::vector<std::pair<uint64_t, uint64_t>> watch;  // address, value at start
};
static std::mutex g_mu;
static std::condition_variable g_cv;
static std::deque<std::shared_ptr<Req>> g_queue;      // filled by socket threads
static std::vector<std::shared_ptr<Req>> g_waiting;   // owned by the frame thread
static std::atomic<uint64_t> g_frames{0};

static void finish(const std::shared_ptr<Req>& r, bool ok, const std::string& body) {
    std::string out = "{\"id\":" + r->id_json + ",\"ok\":" + (ok ? "true" : "false") + "," +
                      (ok ? "\"result\":" : "\"error\":") + body + "}\n";
    std::lock_guard<std::mutex> l(g_mu);
    r->reply = out;
    r->done = true;
    g_cv.notify_all();
}
static std::string info_json() {
    char* g = gs();
    char b[256];
    bool ingame = g && idler();
    snprintf(b, sizeof b, "{\"ingame\":%s,\"date\":%s,\"player\":%s,\"speed\":%d,\"paused\":%s,\"frame\":%llu}",
             ingame ? "true" : "false", jesc(ingame ? date_now() : "").c_str(), jesc(ingame ? player_tag() : "").c_str(),
             ingame ? *(int*)(g + GS_SPEED) + 1 : 0, paused() == 1 ? "true" : paused() == 0 ? "false" : "null",
             (unsigned long long)g_frames.load());
    return b;
}
static bool console(const std::string& line, std::string& out) {
    std::vector<std::string> args;
    size_t i = 0;
    while (i < line.size()) {
        while (i < line.size() && line[i] == ' ') i++;
        size_t j = i;
        while (j < line.size() && line[j] != ' ') j++;
        if (j > i) args.push_back(line.substr(i, j - i));
        i = j;
    }
    if (args.empty()) { out = "empty command"; return false; }
    if (!*G.conmgr_inst && G.conmgr_create) G.conmgr_create();
    if (!*G.conmgr_inst) { out = "no console manager"; return false; }
    alignas(SResult) char buf[sizeof(SResult)];
    memset(buf, 0, sizeof buf);
    SResult* r = (SResult*)buf;
    G.con_execute(r, *G.conmgr_inst, &args);
    out = r->msg;
    bool ok = r->ok;
    r->~SResult();
    return ok;
}

// ---------- fault guard: a bad pointer in a vm/watch read aborts the request instead of the game ----------
static thread_local sigjmp_buf t_jmp;
static thread_local volatile int t_guard = 0;
static struct sigaction g_old_segv, g_old_bus;
static void fault_handler(int sig, siginfo_t*, void*) {
    if (t_guard) siglongjmp(t_jmp, sig);
    sigaction(sig, sig == SIGSEGV ? &g_old_segv : &g_old_bus, nullptr);  // not ours: restore and re-fault
}
struct Guard {
    Guard() {
        struct sigaction sa{};
        sa.sa_sigaction = fault_handler;
        sa.sa_flags = SA_SIGINFO | SA_NODEFER;
        sigaction(SIGSEGV, &sa, &g_old_segv);
        sigaction(SIGBUS, &sa, &g_old_bus);
    }
    ~Guard() {
        t_guard = 0;
        sigaction(SIGSEGV, &g_old_segv, nullptr);
        sigaction(SIGBUS, &g_old_bus, nullptr);
    }
};

// Runs f with the guard armed; returns 0, or the signal number when f faulted (f's frames are abandoned).
template <class F> __attribute__((noinline)) static int guarded(F&& f) {
    Guard g;
    int sig = sigsetjmp(t_jmp, 0);
    if (sig) { t_guard = 0; return sig; }
    t_guard = 1;
    f();
    t_guard = 0;
    return 0;
}

// ---------- vm: a list of steps run in one frame; step i's value is r<i> ----------
// Values are u64. Operands: number (dec/0x), rN, @symbol, any of them with +off / -off.
//   call F a..      F(a..) up to 8 integer args -> rax          vcall OBJ SLOTOFF a..  (*(*OBJ+SLOTOFF))(OBJ, a..)
//   new SIZE        zeroed ::operator new (caller owns)          buf SIZE               zeroed scratch, freed at end
//   rd1|rd2|rd4|rd8|rs4 ADDR   read (rs4 sign-extends)           wr1|wr4|wr8 ADDR VAL   write
//   str ADDR        read std::string at ADDR -> JSON string      cstr ADDR              read char* -> JSON string
//   mkstr TEXT...   scratch std::string holding TEXT -> its address
//   tag XXX         CCountryTag                                  date                   current date int
//   gs / idler / session / db   well-known pointers
//   post CMD        CSession::Post, deletes CMD when refused -> 1/0
//   del OBJ         virtual deleting destructor (slot 1)
//   clist ADDR [MAX]   walk CList{head,tail,count}: JSON array of element values
//   mem ADDR LEN    hex dump (LEN <= 4096)
typedef uint64_t (*fn8)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
struct VM {
    std::vector<uint64_t> r;
    std::vector<void*> bufs;
    std::vector<std::string*> strs;
    std::string err;
    ~VM() { for (void* b : bufs) free(b); for (auto* s : strs) delete s; }
    bool operand(const std::string& tok, uint64_t& v) {
        size_t cut = tok.find_first_of("+-", 1);
        std::string base = tok.substr(0, cut);
        if (base.empty()) { err = "empty operand"; return false; }
        if (base[0] == 'r' && base.size() > 1 && isdigit((unsigned char)base[1])) {
            size_t i = strtoul(base.c_str() + 1, nullptr, 10);
            if (i >= r.size()) { err = "bad register " + base; return false; }
            v = r[i];
        } else if (base[0] == '@') {
            void* p = dlsym(RTLD_DEFAULT, base.c_str() + 1);
            if (!p) { err = "missing symbol " + base.substr(1); return false; }
            v = (uint64_t)p;
        } else {
            char* e;
            v = strtoull(base.c_str(), &e, 0);
            if (*e) { err = "bad operand " + tok; return false; }
        }
        if (cut != std::string::npos) {
            char* e;
            long long off = strtoll(tok.c_str() + cut, &e, 0);
            if (*e) { err = "bad offset " + tok; return false; }
            v += off;
        }
        return true;
    }
};
static uint64_t get_tag(const char* t) {
    static void** db = sym<void**>("_ZN16CCountryDataBase10_pInstanceE");
    static uint64_t (*f)(void*, const char*, bool) = sym<uint64_t (*)(void*, const char*, bool)>("_ZNK16CCountryDataBase6GetTagEPKcb");
    return db && *db && f ? f(*db, t, false) : 0;
}
static std::string hex64(uint64_t v) { char b[24]; snprintf(b, sizeof b, "\"0x%llx\"", (unsigned long long)v); return b; }

// returns false on error (err set); out = JSON array of per-step results
static bool vm_run(const std::vector<JVal>& steps, std::string& out, std::string& err) {
    VM vm;
    out = "[";
    volatile size_t si = 0;
    int sig = guarded([&] {
    for (si = 0; si < steps.size(); si++) {
        if (steps[si].t != JVal::STR) { vm.err = "step is not a string"; break; }
        const std::string& line = steps[si].s;
        std::vector<std::string> t;
        size_t i = 0;
        while (i < line.size()) {
            while (i < line.size() && line[i] == ' ') i++;
            size_t j = i;
            while (j < line.size() && line[j] != ' ') j++;
            if (j > i) t.push_back(line.substr(i, j - i));
            i = j;
        }
        if (t.empty()) { vm.err = "empty step"; break; }
        const std::string& op = t[0];
        uint64_t a[9] = {0};
        size_t na = 0;
        bool raw = op == "mkstr" || op == "tag";
        if (!raw) {
            for (size_t k = 1; k < t.size() && na < 9; k++, na++)
                if (!vm.operand(t[k], a[na])) break;
            if (!vm.err.empty()) break;
        }
        uint64_t v = 0;
        std::string js;  // non-empty: JSON to emit instead of the hex value
        auto need = [&](size_t n) { if (na < n) vm.err = op + " needs " + std::to_string(n) + " operands"; return na >= n; };
        if (op == "call") { if (!need(1)) break; v = ((fn8)a[0])(a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8]); }
        else if (op == "vcall") { if (!need(2)) break; fn8 f = *(fn8*)(*(char**)a[0] + a[1]); v = f(a[0], a[2], a[3], a[4], a[5], a[6], a[7], a[8]); }
        else if (op == "new") { if (!need(1)) break; v = (uint64_t)::operator new(a[0]); memset((void*)v, 0, a[0]); }
        else if (op == "buf") { if (!need(1)) break; void* b = calloc(1, a[0] ? a[0] : 1); vm.bufs.push_back(b); v = (uint64_t)b; }
        else if (op == "rd1") { if (!need(1)) break; v = *(uint8_t*)a[0]; }
        else if (op == "rd2") { if (!need(1)) break; v = *(uint16_t*)a[0]; }
        else if (op == "rd4") { if (!need(1)) break; v = *(uint32_t*)a[0]; }
        else if (op == "rs4") { if (!need(1)) break; v = (uint64_t)(int64_t) * (int32_t*)a[0]; }
        else if (op == "rd8") { if (!need(1)) break; v = *(uint64_t*)a[0]; }
        else if (op == "wr1") { if (!need(2)) break; *(uint8_t*)a[0] = (uint8_t)a[1]; }
        else if (op == "wr4") { if (!need(2)) break; *(uint32_t*)a[0] = (uint32_t)a[1]; }
        else if (op == "wr8") { if (!need(2)) break; *(uint64_t*)a[0] = a[1]; }
        else if (op == "str") { if (!need(1)) break; std::string* s = (std::string*)a[0]; v = s->size(); js = jesc(s->size() < 65536 ? *s : std::string("<too long>")); }
        else if (op == "cstr") { if (!need(1)) break; const char* s = (const char*)a[0]; js = jesc(s ? std::string(s, strnlen(s, 4096)) : std::string()); }
        else if (op == "mkstr") { size_t p = line.find("mkstr"); std::string txt = line.size() > p + 6 ? line.substr(p + 6) : ""; auto* s = new std::string(txt); vm.strs.push_back(s); v = (uint64_t)s; }
        else if (op == "tag") { if (t.size() < 2) { vm.err = "tag needs a tag"; break; } v = get_tag(t[1].c_str()); }
        else if (op == "date") { char* gg = gs(); v = gg ? (uint64_t) * (int*)(gg + GS_DATE) : 0; }
        else if (op == "gs") v = (uint64_t)gs();
        else if (op == "idler") v = (uint64_t)idler();
        else if (op == "session") v = (uint64_t)session();
        else if (op == "db") { static void** db = sym<void**>("_ZN16CCountryDataBase10_pInstanceE"); v = db ? (uint64_t)*db : 0; }
        else if (op == "post") {
            if (!need(1)) break;
            void* s = session();
            if (!s) { vm.err = "not in a game"; break; }
            v = G.post(s, (void*)a[0]) ? 1 : 0;
            if (!v) (*(void (**)(void*))(*(char**)a[0] + 8))((void*)a[0]);
        }
        else if (op == "del") { if (!need(1)) break; if (a[0]) (*(void (**)(void*))(*(char**)a[0] + 8))((void*)a[0]); }
        else if (op == "clist") {
            if (!need(1)) break;
            uint64_t max = na > 1 ? a[1] : 2000, n = 0;
            js = "[";
            for (char* node = *(char**)a[0]; node && n < max; node = *(char**)(node + 0x10), n++) {
                if (n) js += ",";
                js += hex64(*(uint64_t*)node);
            }
            js += "]";
            v = n;
        }
        else if (op == "mem") {
            if (!need(2)) break;
            if (a[1] > 4096) { vm.err = "mem length > 4096"; break; }
            static const char* hx = "0123456789abcdef";
            js = "\"";
            for (uint64_t k = 0; k < a[1]; k++) { uint8_t b = *(uint8_t*)(a[0] + k); js += hx[b >> 4]; js += hx[b & 15]; }
            js += "\"";
        }
        else { vm.err = "unknown step " + op; break; }
        vm.r.push_back(v);
        if (si) out += ",";
        out += js.empty() ? hex64(v) : js;
    }
    });
    if (sig) { err = "fault (signal " + std::to_string(sig) + ") in step " + std::to_string(si); return false; }
    if (!vm.err.empty()) { err = "step " + std::to_string(si) + ": " + vm.err; return false; }
    out += "]";
    return true;
}

static void handle(const std::shared_ptr<Req>& r) {
    std::string op = jstr(r->o, "op");
    if (op == "ping") {
        finish(r, true, std::string("{\"version\":\"" HOOK_VERSION "\",\"symbols_ok\":") + (G.ok ? "true" : "false") +
                            ",\"frame\":" + std::to_string(g_frames.load()) + "}");
    } else if (!G.ok) {
        finish(r, false, jesc("hook symbols missing; see game stderr"));
    } else if (op == "info") {
        finish(r, true, info_json());
    } else if (op == "console") {
        std::string out;
        bool ok = console(jstr(r->o, "line"), out);
        finish(r, true, std::string("{\"ok\":") + (ok ? "true" : "false") + ",\"output\":" + jesc(out) + "}");
    } else if (op == "pause" || op == "speed") {  // via the console's own speed command (0 pauses, N unpauses at N)
        char* g = gs();
        if (!g || !idler()) { finish(r, false, jesc("not in a game")); return; }
        std::string o;
        int cur = *(int*)(g + GS_SPEED) + 1;
        bool was_paused = paused() == 1;
        if (op == "pause") {
            auto it = r->o.find("on");
            bool on = it == r->o.end() || it->second.t != JVal::BOOL || it->second.b;
            if (on != was_paused) console(on ? "speed 0" : "speed " + std::to_string(cur), o);
        } else {
            int v = (int)jnum(r->o, "value", 0);
            if (v < 1 || v > 5) { finish(r, false, jesc("speed must be 1-5")); return; }
            console("speed " + std::to_string(v), o);
            if (was_paused) console("speed 0", o);
        }
        finish(r, true, info_json());
    } else if (op == "speed_up") {  // proof of CSession::Post: posts CIncreaseGameSpeedCommand
        void* s = session();
        if (!s || !G.inc_speed_ctor) { finish(r, false, jesc("not in a game")); return; }
        void* cmd = ::operator new(0x48);
        G.inc_speed_ctor(cmd);
        bool posted = G.post(s, cmd);
        if (!posted) (*(void (**)(void*))(*(char**)cmd + 8))(cmd);  // deleting dtor: Post does not free on false
        finish(r, true, std::string("{\"posted\":") + (posted ? "true" : "false") + "}");
    } else if (op == "wait") {  // {"until":"1445.1.1","timeout":60,"stop_on_pause":true}
        if (!gs() || !idler()) { finish(r, false, jesc("not in a game")); return; }
        r->until_key = date_key(jstr(r->o, "until"));
        if (r->until_key < 0) { finish(r, false, jesc("until must be Y.M.D")); return; }
        double to = jnum(r->o, "timeout", 60);
        r->deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds((long)(to * 1000));
        auto it = r->o.find("stop_on_pause");
        r->stop_on_pause = it == r->o.end() || it->second.t != JVal::BOOL || it->second.b;
        it = r->o.find("pause");
        r->pause_at_end = it != r->o.end() && it->second.t == JVal::BOOL && it->second.b;
        it = r->o.find("watch");  // array of address strings: finish with reason "changed" when a u64 there changes
        if (it != r->o.end() && it->second.t == JVal::ARR) {
            const auto& arr = it->second.a;
            if (guarded([&] {
                    for (auto& w : arr) {
                        uint64_t ad = w.t == JVal::STR ? strtoull(w.s.c_str(), nullptr, 0) : (uint64_t)w.n;
                        r->watch.push_back({ad, *(uint64_t*)ad});
                    }
                })) { finish(r, false, jesc("watch address faults")); return; }
        }
        r->waiting = true;
        g_waiting.push_back(r);
    } else if (op == "vm") {
        auto it = r->o.find("steps");
        if (it == r->o.end() || it->second.t != JVal::ARR) { finish(r, false, jesc("steps must be an array of strings")); return; }
        std::string out, err;
        if (vm_run(it->second.a, out, err)) finish(r, true, out);
        else finish(r, false, jesc(err));
    } else {
        finish(r, false, jesc("unknown op " + op));
    }
}

static void check_waits() {
    if (g_waiting.empty()) return;
    std::string d = gs() && idler() ? date_now() : "";
    long k = date_key(d);
    int p = paused();
    auto now = std::chrono::steady_clock::now();
    for (size_t i = 0; i < g_waiting.size();) {
        auto& r = g_waiting[i];
        const char* why = nullptr;
        if (d.empty()) why = "left_game";
        else if (k >= r->until_key) why = "reached";
        else if (r->stop_on_pause && p == 1) why = "paused";
        else if (now >= r->deadline) why = "timeout";
        int changed = -1;
        if (!why && !r->watch.empty()) {
            if (guarded([&] {
                    for (size_t w = 0; w < r->watch.size(); w++)
                        if (*(uint64_t*)r->watch[w].first != r->watch[w].second) { changed = (int)w; break; }
                })) why = "watch_fault";
            else if (changed >= 0) why = "changed";
        }
        if (why) {
            if (r->pause_at_end && !d.empty() && p == 0) { std::string o; console("speed 0", o); }
            finish(r, true, std::string("{\"reason\":\"") + why + "\",\"date\":" + jesc(d) + ",\"changed\":" + std::to_string(changed) + "}");
            g_waiting.erase(g_waiting.begin() + i);
        } else i++;
    }
}

// Frame cap (EU4_MCP_FPS=30): sleep to an absolute deadline so frames are evenly spaced; 0 or unset = no cap.
static void frame_cap() {
    static long ns = -1;
    static struct timespec next;
    if (ns < 0) {
        const char* e = getenv("EU4_MCP_FPS");
        int fps = e ? atoi(e) : 0;
        ns = fps > 0 ? 1000000000L / fps : 0;
        clock_gettime(CLOCK_MONOTONIC, &next);
    }
    if (!ns) return;
    next.tv_nsec += ns;
    if (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    if (now.tv_sec > next.tv_sec || (now.tv_sec == next.tv_sec && now.tv_nsec >= next.tv_nsec)) { next = now; return; }  // running late: no sleep
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, nullptr);
}

static void frame_tick() {
    g_frames++;
    frame_cap();
    for (int n = 0; n < 8; n++) {
        std::shared_ptr<Req> r;
        {
            std::lock_guard<std::mutex> l(g_mu);
            if (g_queue.empty()) break;
            r = g_queue.front();
            g_queue.pop_front();
        }
        handle(r);
    }
    check_waits();
}

// ---------- socket server ----------
static void serve_client(int fd) {
    std::string buf;
    char tmp[4096];
    for (;;) {
        ssize_t n = read(fd, tmp, sizeof tmp);
        if (n <= 0) break;
        buf.append(tmp, n);
        size_t nl;
        while ((nl = buf.find('\n')) != std::string::npos) {
            std::string line = buf.substr(0, nl);
            buf.erase(0, nl + 1);
            if (line.empty()) continue;
            auto r = std::make_shared<Req>();
            std::string reply;
            if (!parse_obj(line.c_str(), r->o)) {
                reply = "{\"id\":null,\"ok\":false,\"error\":\"bad json\"}\n";
            } else {
                auto it = r->o.find("id");
                if (it != r->o.end()) {
                    if (it->second.t == JVal::NUM) { char b[32]; snprintf(b, sizeof b, "%.0f", it->second.n); r->id_json = b; }
                    else if (it->second.t == JVal::STR) r->id_json = jesc(it->second.s);
                }
                std::unique_lock<std::mutex> l(g_mu);
                g_queue.push_back(r);
                g_cv.wait(l, [&] { return r->done; });
                reply = r->reply;
            }
            if (write(fd, reply.data(), reply.size()) < 0) { close(fd); return; }
        }
    }
    close(fd);
}
static void server_thread() {
    const char* st = getenv("EU4_MCP_STATE");
    std::string dir = st ? st : std::string(getenv("HOME") ? getenv("HOME") : "/tmp") + "/.config/eu4-mcp";
    mkdir(dir.c_str(), 0700);
    std::string path = dir + "/hook.sock";
    unlink(path.c_str());
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un a{};
    a.sun_family = AF_UNIX;
    strncpy(a.sun_path, path.c_str(), sizeof a.sun_path - 1);
    if (s < 0 || bind(s, (sockaddr*)&a, sizeof a) < 0 || listen(s, 8) < 0) {
        perror("[eu4hook] socket");
        return;
    }
    chmod(path.c_str(), 0600);
    fprintf(stderr, "[eu4hook] %s listening on %s, symbols %s\n", HOOK_VERSION, path.c_str(), G.ok ? "ok" : "MISSING");
    for (;;) {
        int c = accept(s, nullptr, nullptr);
        if (c < 0) continue;
        std::thread(serve_client, c).detach();
    }
}

// ---------- interposed frame call ----------
static bool g_started = false;
extern "C" void SteamAPI_RunCallbacks() {
    static void (*real)() = (void (*)())dlsym(RTLD_NEXT, "SteamAPI_RunCallbacks");
    if (!g_started) {
        g_started = true;
        resolve();
        std::thread(server_thread).detach();
    }
    char* ra = (char*)__builtin_return_address(0);
    char* f = (char*)G.update_one_frame;
    if (f && ra > f && ra < f + 0x40 && G.is_main_thread && G.is_main_thread()) frame_tick();
    if (real) real();
}
