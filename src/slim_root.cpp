#include <TBranch.h>
#include <TDirectory.h>
#include <TFile.h>
#include <TNamed.h>
#include <TTree.h>

#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace {
constexpr int kCompressionSettings = 509;  // ROOT ZSTD level 9.
constexpr int kBasketBytes = 256 * 1024;
constexpr const char* kVersion = "dyb-slim-v5.1";
constexpr int kAdCount = 4;              // AD detector codes 1-4.
constexpr double kAdMinEnergyMeV = 0.6;  // Lowest tested cut that fits the disk with reserve.
constexpr double kAdBandLowMeV = 0.3;    // Dropped AD entries are counted as <= 0.3 or (0.3, 0.6].
constexpr const char* kSelectionOn =
    "AD entries (detector 1-4) kept when energy > 0.6 MeV; dropped AD entries counted per source "
    "and detector as energy <= 0.3 MeV or NaN (adBelow0p3MeV) and 0.3 < energy <= 0.6 MeV "
    "(adFrom0p3To0p6MeV); every other detector kept; timestamps cover every source trigger";
constexpr const char* kSelectionOff =
    "no selection: every entry kept; AD entries (detector 1-4) still counted per source and "
    "detector as energy <= 0.3 MeV or NaN (adBelow0p3MeV) and 0.3 < energy <= 0.6 MeV "
    "(adFrom0p3To0p6MeV); timestamps cover every source trigger";

bool is_ad(Short_t detector) { return detector >= 1 && detector <= kAdCount; }

// 0 = kept, 1 = at or below 0.3 MeV or NaN, 2 = in (0.3, 0.6] MeV.
int ad_class(Float_t energy) {
  if (energy > kAdMinEnergyMeV) return 0;
  if (energy > kAdBandLowMeV) return 2;
  return 1;
}

const std::vector<std::string> kAdBranches = {
    "site",          "detector",        "triggerType", "triggerNumber", "triggerTimeSec",
    "triggerTimeNanoSec", "energy",     "x",           "y",             "z",
    "runNumber",
};
const std::vector<std::string> kCalibBranches = {
    "nHit", "MiddleTimeRMS", "Quadrant", "Q1", "Q2", "MaxQ",
    "MaxQ_2inchPMT", "Kurtosis", "time_PSD_local_RMS",
};
const std::vector<std::string> kSourceBranches = {
    "path", "firstEntry", "entries", "keptEntries", "inputBytes", "firstTriggerTimeSec",
    "firstTriggerTimeNanoSec", "lastTriggerTimeSec", "lastTriggerTimeNanoSec",
    "dataDurationSeconds", "processingWallSeconds", "processingCpuSeconds",
    "adBelow0p3MeV", "adFrom0p3To0p6MeV",
};

long long file_size(const std::string& path) {
  struct stat status {};
  return stat(path.c_str(), &status) == 0 ? status.st_size : -1;
}

double cpu_seconds() {
  struct rusage usage {};
  if (getrusage(RUSAGE_SELF, &usage) != 0) return 0.0;
  return usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1e6 + usage.ru_stime.tv_sec +
         usage.ru_stime.tv_usec / 1e6;
}

std::vector<std::string> read_inputs(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) throw std::runtime_error("cannot read input list: " + path);
  std::vector<std::string> inputs;
  std::unordered_set<std::string> seen;
  std::string line;
  while (std::getline(stream, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    if (line.find('\t') != std::string::npos)
      throw std::runtime_error("input path contains a tab and cannot be written to TSV");
    if (line.front() != '/') throw std::runtime_error("input path is not absolute: " + line);
    if (!seen.insert(line).second) throw std::runtime_error("duplicate input path: " + line);
    inputs.push_back(line);
  }
  if (inputs.empty()) throw std::runtime_error("input list is empty: " + path);
  return inputs;
}

template <typename T>
void bind(TTree* tree, const char* name, T* address) {
  if (!tree->GetBranch(name)) throw std::runtime_error(std::string("missing branch: ") + name);
  tree->SetBranchStatus(name, true);
  if (tree->SetBranchAddress(name, address) < 0)
    throw std::runtime_error(std::string("cannot bind branch: ") + name);
}

void require_branches(TTree* tree, const std::vector<std::string>& names) {
  for (const auto& name : names)
    if (!tree->GetBranch(name.c_str())) throw std::runtime_error("missing output branch: " + name);
}

long long verify_file(const std::string& path) {
  TFile file(path.c_str(), "READ");
  if (file.IsZombie()) throw std::runtime_error("cannot open output: " + path);
  auto* ad = dynamic_cast<TTree*>(file.Get("Event/Rec/AdSimpleNL"));
  auto* calib = dynamic_cast<TTree*>(file.Get("Event/Data/CalibStats"));
  auto* sources = dynamic_cast<TTree*>(file.Get("Preservation/Sources"));
  if (!ad || !calib || !sources) throw std::runtime_error("output schema is incomplete: " + path);
  require_branches(ad, kAdBranches);
  require_branches(calib, kCalibBranches);
  require_branches(sources, kSourceBranches);
  if (ad->GetEntries() != calib->GetEntries())
    throw std::runtime_error("output entry counts are invalid: " + path);
  if (sources->GetEntries() <= 0) throw std::runtime_error("source manifest is empty: " + path);
  auto* selection = dynamic_cast<TNamed*>(file.Get("Preservation/selection"));
  const std::string selection_title = selection ? selection->GetTitle() : "";
  if (selection_title != kSelectionOn && selection_title != kSelectionOff)
    throw std::runtime_error("output selection metadata is missing or different: " + path);
  const bool enabled = selection_title == kSelectionOn;

  Long64_t first_entry = 0, source_entries = 0, kept_entries = 0, source_bytes = 0;
  Long64_t total_source_entries = 0, total_kept_entries = 0;
  Long64_t ad_below[kAdCount] = {0}, ad_band[kAdCount] = {0};
  Int_t first_sec = 0, first_nano = 0, last_sec = 0, last_nano = 0;
  Double_t data_seconds = 0, wall_seconds = 0, used_cpu_seconds = 0;
  sources->SetBranchAddress("firstEntry", &first_entry);
  sources->SetBranchAddress("entries", &source_entries);
  sources->SetBranchAddress("keptEntries", &kept_entries);
  sources->SetBranchAddress("adBelow0p3MeV", ad_below);
  sources->SetBranchAddress("adFrom0p3To0p6MeV", ad_band);
  sources->SetBranchAddress("inputBytes", &source_bytes);
  sources->SetBranchAddress("firstTriggerTimeSec", &first_sec);
  sources->SetBranchAddress("firstTriggerTimeNanoSec", &first_nano);
  sources->SetBranchAddress("lastTriggerTimeSec", &last_sec);
  sources->SetBranchAddress("lastTriggerTimeNanoSec", &last_nano);
  sources->SetBranchAddress("dataDurationSeconds", &data_seconds);
  sources->SetBranchAddress("processingWallSeconds", &wall_seconds);
  sources->SetBranchAddress("processingCpuSeconds", &used_cpu_seconds);
  for (Long64_t index = 0; index < sources->GetEntries(); ++index) {
    if (sources->GetEntry(index) <= 0 || first_entry != total_kept_entries ||
        source_entries < 0 || kept_entries < 0 || kept_entries > source_entries ||
        source_bytes < 0 || first_nano < 0 ||
        first_nano >= 1000000000 || last_nano < 0 || last_nano >= 1000000000 ||
        !std::isfinite(data_seconds) || data_seconds < 0 || !std::isfinite(wall_seconds) ||
        wall_seconds < 0 || !std::isfinite(used_cpu_seconds) || used_cpu_seconds < 0)
      throw std::runtime_error("invalid source timing record: " + path);
    Long64_t dropped = 0;
    for (int ad_index = 0; ad_index < kAdCount; ++ad_index) {
      if (ad_below[ad_index] < 0 || ad_band[ad_index] < 0)
        throw std::runtime_error("negative AD selection count: " + path);
      dropped += ad_below[ad_index] + ad_band[ad_index];
    }
    if (enabled ? kept_entries + dropped != source_entries
                : kept_entries != source_entries || dropped > source_entries)
      throw std::runtime_error("source selection accounting does not match its entries: " + path);
    const std::int64_t first_ns = static_cast<std::int64_t>(first_sec) * 1000000000LL + first_nano;
    const std::int64_t last_ns = static_cast<std::int64_t>(last_sec) * 1000000000LL + last_nano;
    const double expected_seconds = static_cast<double>(last_ns - first_ns) / 1e9;
    if (last_ns < first_ns || data_seconds != expected_seconds)
      throw std::runtime_error("source duration does not match timestamps: " + path);
    total_source_entries += source_entries;
    total_kept_entries += kept_entries;
  }
  if (total_kept_entries != ad->GetEntries())
    throw std::runtime_error("kept entry total does not match event trees: " + path);
  Short_t detector = 0;
  Float_t energy = 0;
  ad->SetBranchStatus("*", false);
  bind(ad, "detector", &detector);
  bind(ad, "energy", &energy);
  for (Long64_t entry = 0; enabled && entry < ad->GetEntries(); ++entry) {
    if (ad->GetEntry(entry) <= 0) throw std::runtime_error("cannot read output entry: " + path);
    if (is_ad(detector) && ad_class(energy) != 0)
      throw std::runtime_error("output holds an AD entry at or below the selection threshold: " + path);
  }
  std::cout << "VERIFY entries=" << ad->GetEntries() << " source_entries=" << total_source_entries
            << " sources=" << sources->GetEntries() << " selection=" << (enabled ? "on" : "off")
            << " bytes=" << file_size(path) << '\n';
  return ad->GetEntries();
}

void convert(const std::string& input_list, const std::string& output_path,
             const std::string& source_report_path, int run_number, bool enabled) {
  const auto inputs = read_inputs(input_list);
  if (std::filesystem::exists(output_path) || std::filesystem::exists(source_report_path))
    throw std::runtime_error("refusing to overwrite output or source report");
  const std::filesystem::path output(output_path);
  const std::filesystem::path source_report(source_report_path);
  if (std::filesystem::weakly_canonical(output) == std::filesystem::weakly_canonical(source_report))
    throw std::runtime_error("output and source report must be different paths");
  if (output.parent_path().empty() || !std::filesystem::is_directory(output.parent_path()))
    throw std::runtime_error("output directory does not exist: " + output.parent_path().string());
  if (source_report.parent_path().empty() ||
      !std::filesystem::is_directory(source_report.parent_path()))
    throw std::runtime_error("source report directory does not exist: " +
                             source_report.parent_path().string());

  const std::string partial = output_path + ".partial." + std::to_string(getpid());
  const std::string partial_report =
      source_report_path + ".partial." + std::to_string(getpid());
  std::remove(partial.c_str());
  std::remove(partial_report.c_str());
  const auto wall_start = std::chrono::steady_clock::now();
  const double cpu_start = cpu_seconds();
  long long total_entries = 0;
  long long total_kept_entries = 0;
  long long total_input_bytes = 0;

  try {
    TFile output_file(partial.c_str(), "RECREATE", "", kCompressionSettings);
    if (output_file.IsZombie()) throw std::runtime_error("cannot create output: " + partial);
    std::ofstream source_report(partial_report);
    if (!source_report) throw std::runtime_error("cannot create source report: " + partial_report);
    source_report << "sourceIndex\tpath\tentries\tinputBytes\tfirstTriggerTimeSec\t"
                     "firstTriggerTimeNanoSec\tlastTriggerTimeSec\tlastTriggerTimeNanoSec\t"
                     "dataDurationSeconds\tprocessingWallSeconds\tprocessingCpuSeconds\t"
                     "keptEntries";
    for (int ad_index = 1; ad_index <= kAdCount; ++ad_index)
      source_report << "\tad" << ad_index << "Below0p3MeV\tad" << ad_index << "From0p3To0p6MeV";
    source_report << '\n';
    source_report << std::setprecision(std::numeric_limits<double>::max_digits10);
    auto* event_dir = output_file.mkdir("Event");
    auto* rec_dir = event_dir->mkdir("Rec");
    auto* data_dir = event_dir->mkdir("Data");
    auto* preservation_dir = output_file.mkdir("Preservation");

    Short_t site = 0, detector = 0;
    UInt_t trigger_type = 0, trigger_number = 0;
    Int_t trigger_sec = 0, trigger_nano = 0, n_hit = 0;
    Int_t stored_run_number = run_number;
    Float_t energy = 0, x = 0, y = 0, z = 0;
    Float_t middle_time_rms = 0, quadrant = 0, q1 = 0, q2 = 0, max_q = 0;
    Float_t max_q_2inch = 0, kurtosis = 0, time_psd_local_rms = 0;

    rec_dir->cd();
    TTree ad("AdSimpleNL", "Necessary AdSimpleNL reconstruction and event metadata");
    ad.Branch("site", &site, "site/S");
    ad.Branch("detector", &detector, "detector/S");
    ad.Branch("triggerType", &trigger_type, "triggerType/i");
    ad.Branch("triggerNumber", &trigger_number, "triggerNumber/i");
    ad.Branch("triggerTimeSec", &trigger_sec, "triggerTimeSec/I");
    ad.Branch("triggerTimeNanoSec", &trigger_nano, "triggerTimeNanoSec/I");
    ad.Branch("energy", &energy, "energy/F");
    ad.Branch("x", &x, "x/F");
    ad.Branch("y", &y, "y/F");
    ad.Branch("z", &z, "z/F");
    ad.Branch("runNumber", &stored_run_number, "runNumber/I");

    data_dir->cd();
    TTree calib("CalibStats", "Necessary CalibStats variables");
    calib.Branch("nHit", &n_hit, "nHit/I");
    calib.Branch("MiddleTimeRMS", &middle_time_rms, "MiddleTimeRMS/F");
    calib.Branch("Quadrant", &quadrant, "Quadrant/F");
    calib.Branch("Q1", &q1, "Q1/F");
    calib.Branch("Q2", &q2, "Q2/F");
    calib.Branch("MaxQ", &max_q, "MaxQ/F");
    calib.Branch("MaxQ_2inchPMT", &max_q_2inch, "MaxQ_2inchPMT/F");
    calib.Branch("Kurtosis", &kurtosis, "Kurtosis/F");
    calib.Branch("time_PSD_local_RMS", &time_psd_local_rms, "time_PSD_local_RMS/F");

    preservation_dir->cd();
    std::string source_path;
    Long64_t first_entry = 0, source_entries = 0, kept_entries = 0, source_bytes = 0;
    Long64_t ad_below[kAdCount] = {0}, ad_band[kAdCount] = {0};
    Int_t first_trigger_sec = 0, first_trigger_nano = 0;
    Int_t last_trigger_sec = 0, last_trigger_nano = 0;
    Double_t data_duration_seconds = 0;
    Double_t processing_wall_seconds = 0, processing_cpu_seconds = 0;
    TTree sources("Sources", "Input-file provenance");
    sources.Branch("path", &source_path);
    sources.Branch("firstEntry", &first_entry, "firstEntry/L");
    sources.Branch("entries", &source_entries, "entries/L");
    sources.Branch("keptEntries", &kept_entries, "keptEntries/L");
    sources.Branch("inputBytes", &source_bytes, "inputBytes/L");
    sources.Branch("firstTriggerTimeSec", &first_trigger_sec, "firstTriggerTimeSec/I");
    sources.Branch("firstTriggerTimeNanoSec", &first_trigger_nano, "firstTriggerTimeNanoSec/I");
    sources.Branch("lastTriggerTimeSec", &last_trigger_sec, "lastTriggerTimeSec/I");
    sources.Branch("lastTriggerTimeNanoSec", &last_trigger_nano, "lastTriggerTimeNanoSec/I");
    sources.Branch("dataDurationSeconds", &data_duration_seconds, "dataDurationSeconds/D");
    sources.Branch("processingWallSeconds", &processing_wall_seconds, "processingWallSeconds/D");
    sources.Branch("processingCpuSeconds", &processing_cpu_seconds, "processingCpuSeconds/D");
    sources.Branch("adBelow0p3MeV", ad_below, "adBelow0p3MeV[4]/L");
    sources.Branch("adFrom0p3To0p6MeV", ad_band, "adFrom0p3To0p6MeV[4]/L");

    for (TTree* tree : {&ad, &calib, &sources}) {
      tree->SetAutoFlush(0);
      tree->SetAutoSave(0);
      tree->SetBasketSize("*", kBasketBytes);
    }

    for (std::size_t source_index = 0; source_index < inputs.size(); ++source_index) {
      const auto& input_path = inputs[source_index];
      const auto source_wall_start = std::chrono::steady_clock::now();
      const double source_cpu_start = cpu_seconds();
      TFile input_file(input_path.c_str(), "READ");
      if (input_file.IsZombie()) throw std::runtime_error("cannot open input: " + input_path);
      auto* simple = dynamic_cast<TTree*>(input_file.Get("Event/Rec/AdSimpleNL"));
      auto* stats = dynamic_cast<TTree*>(input_file.Get("Event/Data/CalibStats"));
      if (!simple || !stats) throw std::runtime_error("missing required tree: " + input_path);
      if (simple->GetEntries() != stats->GetEntries())
        throw std::runtime_error("tree entry mismatch: " + input_path);

      simple->SetBranchStatus("*", false);
      bind(simple, "site", &site);
      bind(simple, "detector", &detector);
      bind(simple, "triggerType", &trigger_type);
      bind(simple, "triggerNumber", &trigger_number);
      bind(simple, "triggerTimeSec", &trigger_sec);
      bind(simple, "triggerTimeNanoSec", &trigger_nano);
      bind(simple, "energy", &energy);
      bind(simple, "x", &x);
      bind(simple, "y", &y);
      bind(simple, "z", &z);

      stats->SetBranchStatus("*", false);
      bind(stats, "nHit", &n_hit);
      bind(stats, "MiddleTimeRMS", &middle_time_rms);
      bind(stats, "Quadrant", &quadrant);
      bind(stats, "Q1", &q1);
      bind(stats, "Q2", &q2);
      bind(stats, "MaxQ", &max_q);
      bind(stats, "MaxQ_2inchPMT", &max_q_2inch);
      bind(stats, "Kurtosis", &kurtosis);
      bind(stats, "time_PSD_local_RMS", &time_psd_local_rms);

      source_path = input_path;
      first_entry = total_kept_entries;
      source_entries = simple->GetEntries();
      kept_entries = 0;
      for (int ad_index = 0; ad_index < kAdCount; ++ad_index) ad_below[ad_index] = ad_band[ad_index] = 0;
      source_bytes = file_size(input_path);
      if (source_bytes < 0) throw std::runtime_error("cannot stat input: " + input_path);
      total_input_bytes += source_bytes;

      std::int64_t first_timestamp_ns = 0, last_timestamp_ns = 0;
      for (Long64_t entry = 0; entry < source_entries; ++entry) {
        if (simple->GetEntry(entry) <= 0 || stats->GetEntry(entry) <= 0)
          throw std::runtime_error("read failure in input: " + input_path);
        const std::int64_t timestamp_ns =
            static_cast<std::int64_t>(trigger_sec) * 1000000000LL + trigger_nano;
        if (entry == 0 || timestamp_ns < first_timestamp_ns) first_timestamp_ns = timestamp_ns;
        if (entry == 0 || timestamp_ns > last_timestamp_ns) last_timestamp_ns = timestamp_ns;
        if (is_ad(detector)) {
          const int selection_class = ad_class(energy);
          if (selection_class == 1) ++ad_below[detector - 1];
          if (selection_class == 2) ++ad_band[detector - 1];
          if (enabled && selection_class != 0) continue;
        }
        if (ad.Fill() < 0 || calib.Fill() < 0)
          throw std::runtime_error("cannot write output event");
        ++kept_entries;
      }
      if (ad.FlushBaskets() < 0 || calib.FlushBaskets() < 0)
        throw std::runtime_error("cannot flush output events");
      input_file.Close();

      if (source_entries > 0) {
        first_trigger_sec = static_cast<Int_t>(first_timestamp_ns / 1000000000LL);
        first_trigger_nano = static_cast<Int_t>(first_timestamp_ns % 1000000000LL);
        last_trigger_sec = static_cast<Int_t>(last_timestamp_ns / 1000000000LL);
        last_trigger_nano = static_cast<Int_t>(last_timestamp_ns % 1000000000LL);
        data_duration_seconds = static_cast<double>(last_timestamp_ns - first_timestamp_ns) / 1e9;
      } else {
        first_trigger_sec = first_trigger_nano = last_trigger_sec = last_trigger_nano = 0;
        data_duration_seconds = 0;
      }
      processing_wall_seconds = std::chrono::duration<double>(
          std::chrono::steady_clock::now() - source_wall_start).count();
      processing_cpu_seconds = cpu_seconds() - source_cpu_start;
      if (sources.Fill() < 0) throw std::runtime_error("cannot write source provenance");
      source_report << source_index << '\t' << source_path << '\t' << source_entries << '\t'
                    << source_bytes << '\t' << first_trigger_sec << '\t' << first_trigger_nano
                    << '\t' << last_trigger_sec << '\t' << last_trigger_nano << '\t'
                    << data_duration_seconds << '\t' << processing_wall_seconds << '\t'
                    << processing_cpu_seconds << '\t' << kept_entries;
      for (int ad_index = 0; ad_index < kAdCount; ++ad_index)
        source_report << '\t' << ad_below[ad_index] << '\t' << ad_band[ad_index];
      source_report << '\n';
      total_entries += source_entries;
      total_kept_entries += kept_entries;
    }

    rec_dir->cd();
    if (ad.Write() <= 0) throw std::runtime_error("cannot write AD tree");
    data_dir->cd();
    if (calib.Write() <= 0) throw std::runtime_error("cannot write CalibStats tree");
    preservation_dir->cd();
    if (sources.Write() <= 0) throw std::runtime_error("cannot write source tree");
    TNamed selection("selection", enabled ? kSelectionOn : kSelectionOff);
    if (selection.Write() <= 0 || output_file.TestBit(TFile::kWriteError))
      throw std::runtime_error("cannot finish output metadata");
    output_file.Close();
    source_report.close();
    if (!source_report) throw std::runtime_error("cannot finish source report: " + partial_report);
    verify_file(partial);
    if (::link(partial_report.c_str(), source_report_path.c_str()) != 0)
      throw std::runtime_error("cannot atomically publish source report: " + source_report_path);
    std::remove(partial_report.c_str());
    if (::link(partial.c_str(), output_path.c_str()) != 0) {
      std::remove(source_report_path.c_str());
      throw std::runtime_error("cannot atomically publish output: " + output_path);
    }
    std::remove(partial.c_str());
  } catch (...) {
    std::remove(partial.c_str());
    std::remove(partial_report.c_str());
    throw;
  }

  const double wall_seconds =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - wall_start).count();
  const double used_cpu_seconds = cpu_seconds() - cpu_start;
  std::cout << "RESULT version=" << kVersion << " selection=" << (enabled ? "on" : "off")
            << " sources=" << inputs.size()
            << " entries=" << total_entries << " kept_entries=" << total_kept_entries
            << " input_bytes=" << total_input_bytes
            << " output_bytes=" << file_size(output_path) << " wall_seconds=" << wall_seconds
            << " cpu_seconds=" << used_cpu_seconds << '\n';
}
}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc == 2 && std::string(argv[1]) == "--version") {
      std::cout << kVersion << '\n';
      return 0;
    }
    if (argc == 3 && std::string(argv[1]) == "--verify") {
      verify_file(argv[2]);
      return 0;
    }
    const std::string usage =
        "usage: slim-root --input-list FILE --output FILE --source-report FILE "
        "--run-number NUMBER [--ad-selection on|off]\n"
        "       slim-root --verify FILE | --version | --help\n"
        "Stores aligned AdSimpleNL and CalibStats trees. With --ad-selection on, "
        "AD detectors 1-4 require energy > 0.6 MeV; all non-AD events are kept without an nHit cut.";
    if (argc == 2 && std::string(argv[1]) == "--help") {
      std::cout << usage << '\n';
      return 0;
    }
    std::string input_list, output, source_report, selection = "off";
    int run_number = -1;
    for (int i = 1; i < argc; ++i) {
      const std::string arg = argv[i];
      if (arg == "--input-list" && i + 1 < argc) input_list = argv[++i];
      else if (arg == "--output" && i + 1 < argc) output = argv[++i];
      else if (arg == "--source-report" && i + 1 < argc) source_report = argv[++i];
      else if (arg == "--run-number" && i + 1 < argc) {
        const std::string value = argv[++i];
        if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
          throw std::runtime_error("run number must be a nonnegative integer");
        run_number = std::stoi(value);
      }
      else if (arg == "--ad-selection" && i + 1 < argc) selection = argv[++i];
      else throw std::runtime_error("unknown or incomplete argument: " + arg);
    }
    if (input_list.empty() || output.empty() || source_report.empty() || run_number < 0 ||
        (selection != "on" && selection != "off"))
      throw std::runtime_error(usage);
    convert(input_list, output, source_report, run_number, selection == "on");
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ERROR " << error.what() << '\n';
    return 1;
  }
}
