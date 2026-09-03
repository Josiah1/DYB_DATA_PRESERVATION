// Count AdSimpleNL entries per detector with AD-energy tallies and a 0.1 MeV histogram.
#include <TFile.h>
#include <TTree.h>

#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

int main(int argc, char** argv) {
  if (argc != 2) { std::cerr << "usage: adsel_count LIST\n"; return 2; }
  constexpr int kBins = 100;      // 0.1 MeV bins, 0-10 MeV
  constexpr double kBinWidth = 0.1;
  std::ifstream list(argv[1]);
  std::string path;
  std::cout << "path\tdetector\tentries\tgt0p3\tgt0p7\tnonpositive";
  for (int b = 0; b < kBins; ++b) std::cout << "\tb" << b;
  std::cout << "\toverflow\n";
  while (std::getline(list, path)) {
    if (path.empty()) continue;
    TFile file(path.c_str(), "READ");
    if (file.IsZombie()) { std::cerr << "ERROR cannot open " << path << '\n'; continue; }
    auto* tree = dynamic_cast<TTree*>(file.Get("Event/Rec/AdSimpleNL"));
    if (!tree) { std::cerr << "ERROR missing AdSimpleNL " << path << '\n'; continue; }
    Short_t detector = 0;
    Float_t energy = 0;
    tree->SetBranchStatus("*", false);
    tree->SetBranchStatus("detector", true);
    tree->SetBranchStatus("energy", true);
    tree->SetBranchAddress("detector", &detector);
    tree->SetBranchAddress("energy", &energy);
    struct Tally {
      long long entries = 0, gt03 = 0, gt07 = 0, nonpos = 0, overflow = 0;
      std::vector<long long> hist = std::vector<long long>(kBins, 0);
    };
    std::map<int, Tally> tallies;
    const Long64_t n = tree->GetEntries();
    for (Long64_t i = 0; i < n; ++i) {
      tree->GetEntry(i);
      Tally& t = tallies[detector];
      ++t.entries;
      if (energy > 0.3) ++t.gt03;
      if (energy > 0.7) ++t.gt07;
      if (!(energy > 0)) {
        ++t.nonpos;
      } else {
        const int b = static_cast<int>(energy / kBinWidth);
        if (b >= kBins) ++t.overflow; else ++t.hist[b];
      }
    }
    for (const auto& [det, t] : tallies) {
      std::cout << path << '\t' << det << '\t' << t.entries << '\t' << t.gt03 << '\t'
                << t.gt07 << '\t' << t.nonpos;
      for (auto v : t.hist) std::cout << '\t' << v;
      std::cout << '\t' << t.overflow << '\n';
    }
    std::cout.flush();
    std::cerr << "DONE " << path << " entries=" << n << '\n';
  }
  return 0;
}
