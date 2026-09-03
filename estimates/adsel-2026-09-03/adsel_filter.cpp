// Re-write a slim chunk into selection variants with the production compression
// settings (ZSTD 7, flush at source boundaries) and report entries and bytes.
#include <TDirectory.h>
#include <TFile.h>
#include <TTree.h>

#include <sys/stat.h>

#include <iostream>
#include <string>
#include <vector>

namespace {
constexpr int kCompressionSettings = 507;

long long file_size(const std::string& path) {
  struct stat status {};
  return stat(path.c_str(), &status) == 0 ? status.st_size : -1;
}

struct Variant {
  std::string name;
  double min_ad_energy;
  bool keep_non_ad;
  TFile* file = nullptr;
  TTree* ad = nullptr;
  TTree* calib = nullptr;
  long long kept = 0;
  std::string path;
};
}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) { std::cerr << "usage: adsel_filter SLIM OUTDIR TAG\n"; return 2; }
  const std::string slim = argv[1], outdir = argv[2], tag = argv[3];
  TFile input(slim.c_str(), "READ");
  if (input.IsZombie()) { std::cerr << "cannot open " << slim << '\n'; return 1; }
  auto* ad = dynamic_cast<TTree*>(input.Get("Event/Rec/AdSimpleNL"));
  auto* calib = dynamic_cast<TTree*>(input.Get("Event/Data/CalibStats"));
  auto* sources = dynamic_cast<TTree*>(input.Get("Preservation/Sources"));
  if (!ad || !calib || !sources || ad->GetEntries() != calib->GetEntries()) {
    std::cerr << "bad slim file " << slim << '\n';
    return 1;
  }

  Short_t site = 0, detector = 0;
  UInt_t trigger_type = 0, trigger_number = 0;
  Int_t trigger_sec = 0, trigger_nano = 0, run_number = 0, n_hit = 0;
  Float_t energy = 0, x = 0, y = 0, z = 0;
  Float_t middle_time_rms = 0, quadrant = 0, q1 = 0, q2 = 0, max_q = 0;
  Float_t max_q_2inch = 0, kurtosis = 0, time_psd_local_rms = 0;
  ad->SetBranchAddress("site", &site);
  ad->SetBranchAddress("detector", &detector);
  ad->SetBranchAddress("triggerType", &trigger_type);
  ad->SetBranchAddress("triggerNumber", &trigger_number);
  ad->SetBranchAddress("triggerTimeSec", &trigger_sec);
  ad->SetBranchAddress("triggerTimeNanoSec", &trigger_nano);
  ad->SetBranchAddress("energy", &energy);
  ad->SetBranchAddress("x", &x);
  ad->SetBranchAddress("y", &y);
  ad->SetBranchAddress("z", &z);
  ad->SetBranchAddress("runNumber", &run_number);
  calib->SetBranchAddress("nHit", &n_hit);
  calib->SetBranchAddress("MiddleTimeRMS", &middle_time_rms);
  calib->SetBranchAddress("Quadrant", &quadrant);
  calib->SetBranchAddress("Q1", &q1);
  calib->SetBranchAddress("Q2", &q2);
  calib->SetBranchAddress("MaxQ", &max_q);
  calib->SetBranchAddress("MaxQ_2inchPMT", &max_q_2inch);
  calib->SetBranchAddress("Kurtosis", &kurtosis);
  calib->SetBranchAddress("time_PSD_local_RMS", &time_psd_local_rms);

  std::vector<Long64_t> boundaries;
  {
    Long64_t first_entry = 0, entries = 0;
    sources->SetBranchAddress("firstEntry", &first_entry);
    sources->SetBranchAddress("entries", &entries);
    for (Long64_t i = 0; i < sources->GetEntries(); ++i) {
      sources->GetEntry(i);
      boundaries.push_back(first_entry + entries);
    }
  }

  std::vector<Variant> variants = {
      {"all", -1e30, true},   {"ad03", 0.3, false},  {"ad07", 0.7, false},
      {"ad03wp", 0.3, true},  {"ad07wp", 0.7, true},
  };
  for (auto& v : variants) {
    v.path = outdir + "/" + tag + "." + v.name + ".root";
    v.file = new TFile(v.path.c_str(), "RECREATE", "", kCompressionSettings);
    if (v.file->IsZombie()) { std::cerr << "cannot create " << v.path << '\n'; return 1; }
    auto* event_dir = v.file->mkdir("Event");
    auto* rec_dir = event_dir->mkdir("Rec");
    auto* data_dir = event_dir->mkdir("Data");
    rec_dir->cd();
    v.ad = ad->CloneTree(0);
    data_dir->cd();
    v.calib = calib->CloneTree(0);
    v.file->mkdir("Preservation")->cd();
    sources->CloneTree(-1);
  }

  std::size_t boundary_index = 0;
  const Long64_t n = ad->GetEntries();
  for (Long64_t i = 0; i < n; ++i) {
    ad->GetEntry(i);
    calib->GetEntry(i);
    const bool is_ad = detector >= 1 && detector <= 4;
    for (auto& v : variants) {
      const bool keep = is_ad ? (energy > v.min_ad_energy) : v.keep_non_ad;
      if (!keep) continue;
      v.ad->Fill();
      v.calib->Fill();
      ++v.kept;
    }
    if (boundary_index < boundaries.size() && i + 1 == boundaries[boundary_index]) {
      ++boundary_index;
      for (auto& v : variants) {
        v.ad->FlushBaskets();
        v.calib->FlushBaskets();
      }
    }
  }
  std::cout << "tag\tvariant\tentries\tkept\tbytes\n";
  std::cout << tag << "\tinput\t" << n << '\t' << n << '\t' << file_size(slim) << '\n';
  for (auto& v : variants) {
    v.file->Write();
    v.file->Close();
    std::cout << tag << '\t' << v.name << '\t' << n << '\t' << v.kept << '\t'
              << file_size(v.path) << '\n';
  }
  return 0;
}
