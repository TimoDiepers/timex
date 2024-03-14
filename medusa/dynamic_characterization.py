
from typing import Union, Tuple, Optional, Callable
import pandas as pd
import bw2data as bd
import numpy as np
import os
from bw_temporalis.lcia.climate import characterize_methane, characterize_co2
from datetime import datetime, timedelta



class DynamicCharacterization():
    '''
    This class calculates dynamic characterization of life cycle inventories.
    
    Currently, dynamic characterization functions is available for the following emissions:
    - CO2
    - CH4
    
    Characteriztaion functions are retrieved from bw_temporalis, which have them for CO2 and Methane from these publications: 
    https://doi.org/10.5194/acp-13-2793-2013
    http://pubs.acs.org/doi/abs/10.1021/acs.est.5b01118

    First, the dynamic inventory is formatted as a DataFrame, then the characterization functions are applied in the method characterize_dynamic_inventory
    
    '''
    def __init__(self, 
                 #method: str, 
                 #**kwargs,
                 dynamic_inventory: dict,
                 activity_dict: dict,
                 biosphere_dict_rerversed: dict,
                 act_time_mapping_reversed: dict,
                 demand_timing_dict: dict,
                 temporal_grouping: dict,
                                  ):
        self.dynamic_inventory = dynamic_inventory
        self.activity_dict = activity_dict
        self.biosphere_dict_rerversed = biosphere_dict_rerversed
        self.act_time_mapping_reversed = act_time_mapping_reversed
        self.demand_timing_dict = demand_timing_dict
        self.temporal_grouping = temporal_grouping
        self.dynamic_lci_df = self.format_dynamic_inventory_as_dataframe()
        self.levasseur_dcfs = self.add_levasseur_dcfs()

    def add_levasseur_dcfs(self):
        """
        Extracts the yearly radiative forcing values of various GHG based on Levasseur 2010 for 2000 years(https://ciraig.org/index.php/project/dynco2-dynamic-carbon-footprinter/)
        Unit of forcing is W/m2/kg of GHG emitted

        param: None
        return: dict of dicts with the following structure: {ghg: {year: forcing}}
        """
        #Levasseur
        
        #read in excel data
        subfolder_name = 'data'
        file = 'Dynamic_LCAcalculatorv.2.0.xlsm'
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), subfolder_name, file)
        sheet_name = 'FC'
        data = pd.read_excel(file_path, sheet_name=sheet_name)

        #clean data
        data = data.iloc[2:] #Remove the first three empty rows
        data.columns = data.iloc[0] # Set the first row as the header
        data = data.iloc[3:,:] #remove row 0-2 (contain units)
        data = data.rename(columns={data.columns[0]: 'year'}) # replace the first column label (NaN) with 'Year'
        data = data.sort_values('year') #sort by year

        #map names of elemental flows
        biopshere_flow_mapping = {'CO2': 'carbon dioxide', 'CH4': 'methane', 'N2O': "nitrous oxide", "CO": "carbon monoxide"} #TODO think about impleenting this for biosphere 3
        data = data.rename(columns=biopshere_flow_mapping)

        # Convert DataFrame to dictionary
        levasseur_radiative_forcing = {}
        for column in data.columns[1:]: #skipping first column (year)
            levasseur_radiative_forcing[column] = dict(zip(data.iloc[:, 0], data[column])) #{year: forcing}    
        return levasseur_radiative_forcing
    
    def format_dynamic_inventory_as_dataframe(self):
        
        ''' bring the dynamic inventory into the right format to use the characterization functions from bw_temporalis
        Format needs to be: 
        | date | amount | flow | activity |
        |------|--------|------|----------|
        | 101  | 33     | 1    | 2        |
        | 102  | 32     | 1    | 2        |
        | 103  | 31     | 1    | 2        |
        
        date is datetime
        flow = flow id 
        activity = activity id 
        '''
        flow_mapping= {}
        for key, values in self.dynamic_inventory.items(): 
            flow_mapping[key] = bd.get_node(code=key).id 

        dfs = []
        for key, values in self.dynamic_inventory.items():

            df = pd.DataFrame(values)
            df['flow'] = key
            df['flow'] = df['flow'].replace(flow_mapping) #replace code (uuid) with id

            df.rename(columns={'time': 'date'}, inplace=True) 
            df.rename(columns={'emitting_process': 'activity'}, inplace=True)                  
            
            dfs.append(df)

        inventory_as_dataframe = pd.concat(dfs, ignore_index=True)
        return inventory_as_dataframe
    
    
    def characterize_dynamic_inventory(self, 
                                        #characterization_dictionary: dict, # dictionary: mapping elemental flow (key) with characterization function (value) 
        #temporal_grouping: str,  #default is yearly defined at instance of MedusaLCA
        cumsum: bool | None = True,
        type_of_method: str | None = "radiative_forcing",
        fixed_TH: bool | None = False, #True: Levasseur approach TH for all emissions is calculated from FU, false: TH is calculated from t emission
        TH: int | None = 100, 
        
        flow: set[int] | None = None,
        activity: set[int] | None = None,
        
    ) -> Tuple[pd.DataFrame, str, bool, int]:
        
        ''' 
        #update description
        Function adapted from bw_temporalis to the fact that in comparison to bw_temporalis, our timeline not a Timeline instance, but a normal pd.DataFrame.
        Adjusted to filter the respective elemental flows to be characterized per characterization function, instead of assuming all flows to be categorized.
        
        can receive bw_temporalis characterization functions for CO2 and CH4 or user-defined characterization functions of the same format XZXZ.
        
        The `characterization_function` is applied to each row of the input DataFrame of a timeline for a given `period`. 
        in the case of characterize_co2 and characterize_methane, the timestep is yearly and the time horizon is 100 years
                  
        # TODO add checks, add fixed TH
            
        '''
        if type_of_method not in {"radiative_forcing", "GWP"}:
            raise ValueError(f"impact assessment type must be either 'radiative_forcing' or 'GWP', not {type_of_method}")	
        
        characterization_dictionary = {"carbon dioxide": characterize_co2, "methane": characterize_methane, "carbon monoxide": self.characterize_co, "nitrous oxide": self.characterize_n2o}
        #TODO think if it makes sense to store characterization dictionary here

        time_res_dict = {
        "year": "%Y",
        "month": "%Y%m",
        "day": "%Y%m%d",
        "hour": "%Y%m%d%M",
        }
        all_characterized_inventory = pd.DataFrame()
        
        mapping_flow_to_id = {flow: bd.get_activity(name=flow).id for flow in characterization_dictionary.keys()}

        for characterized_flow, characterization_function in characterization_dictionary.items():
            df = self.dynamic_lci_df.copy()
             
            df = df.loc[self.dynamic_lci_df["flow"]==mapping_flow_to_id[characterized_flow]] #subset of the inventory including characterized flow
            
            #in case the user specifies additional subsets
            if activity:
                df = df.loc[self.dynamic_lci_df["activity"].isin(activity)]
            if flow:
                df = df.loc[self.dynamic_lci_df["flow"]==flow]
                
            df.reset_index(drop=True, inplace=True)
 
            if type_of_method == "radiative_forcing":

                if not fixed_TH: #conventional approach, emission is calculated from t emission for the length of TH, regardless of when emission occur
                    characterized_inventory = pd.concat(
                    [characterization_function(row, period = TH) for _, row in df.iterrows()]
                    )
                    
                else: #fixed TH = True: Levasseur approach: TH for all emissions is calculated from t of FU for the length of TH. 
                    #An emission occur n years before FU is characterized for TH+n years
                    timing_FU = [value for value in self.demand_timing_dict.values()] # what if there are multiple FUs?
                    end_TH_FU = [x + TH for x in timing_FU]
                    end_TH_FU = datetime.strptime(str(end_TH_FU[0]), time_res_dict[self.temporal_grouping])
                    
                    characterized_inventory=pd.DataFrame() #initialize empty df
                    
                    for _, row in df.iterrows():

                        timing_emission = row["date"].to_pydatetime() # convert'pandas._libs.tslibs.timestamps.Timestamp' to datetime object 
                        new_TH = round((end_TH_FU - timing_emission).days / 365.25) #time difference in integer years between emission timing and end of TH of FU
                        characterized_inventory = pd.concat(
                        [characterized_inventory, characterization_function(row, period = new_TH)]
                        )


            if type_of_method == "GWP": #scale radiative forcing to GWP in CO2 equivalent

                characterized_inventory = pd.DataFrame()

                if not fixed_TH: #conventional approach, emission is calculated from t emission for the length of TH, regardless of when emission occur
                    for _, row in df.iterrows():
                        radiative_forcing_ghg = characterization_function(row, period = TH) 
                        row["amount"] = 1 #convert 1 kg CO2 equ.
                        radiative_forcing_co2 = characterize_co2(row, period = TH)

                        ghg_integral = radiative_forcing_ghg["amount"].sum() 
                        co2_integral = radiative_forcing_co2["amount"].sum()

                        co2_equiv = ghg_integral / co2_integral

                        row_data = {
                                'date': radiative_forcing_ghg.loc[0, 'date'], #start date of emission
                                'amount': co2_equiv, #ghg emission in co2 equiv
                                'flow': radiative_forcing_ghg.loc[0, 'flow'],  
                                'activity': radiative_forcing_ghg.loc[0, 'activity'],
                            }
                        row_df = pd.DataFrame([row_data])
                                            
                        characterized_inventory = pd.concat([characterized_inventory, row_df], ignore_index=True)

                else: #fixed TH = True: Levasseur approach: TH for all emissions is calculated from t of FU for the length of TH.
                    timing_FU = [value for value in self.demand_timing_dict.values()] # what if there are multiple FUs?
                    end_TH_FU = [x + TH for x in timing_FU]
                    end_TH_FU = datetime.strptime(str(end_TH_FU[0]), time_res_dict[self.temporal_grouping])
                    
                                        
                    for _, row in df.iterrows():
                        timing_emission = row["date"].to_pydatetime() # convert'pandas._libs.tslibs.timestamps.Timestamp' to datetime object 
                        new_TH = round((end_TH_FU - timing_emission).days / 365.25) 

                        radiative_forcing_ghg = characterization_function(row, period = new_TH) #indidvidual emissions are calculated for t_emission until t_FU + TH
                        row["amount"] = 1 #convert 1 kg CO2 equ.
                        radiative_forcing_co2 = characterize_co2(row, period = TH) # reference substance CO2 is calculated for TH (usually 100 years)

                        ghg_integral = radiative_forcing_ghg["amount"].sum() 
                        co2_integral = radiative_forcing_co2["amount"].sum()

                        co2_equiv = ghg_integral / co2_integral #integral is now relative to TH from FU (=TH)

                        row_data = {
                                'date': radiative_forcing_ghg.loc[0, 'date'], #start date of emission
                                'amount': co2_equiv, #ghg emission in co2 equiv
                                'flow': radiative_forcing_ghg.loc[0, 'flow'],  
                                'activity': radiative_forcing_ghg.loc[0, 'activity'],
                            }
                        row_df = pd.DataFrame([row_data])
                                            
                        characterized_inventory = pd.concat([characterized_inventory, row_df], ignore_index=True)

            # sort by date
            if "date" in characterized_inventory:
                characterized_inventory.sort_values(by="date", ascending=True, inplace=True)
                characterized_inventory.reset_index(drop=True, inplace=True)
                
            if cumsum and "amount" in characterized_inventory:
                characterized_inventory["amount_sum"] = characterized_inventory["amount"].cumsum() #not sure if cumsum here correct

            all_characterized_inventory = pd.concat([all_characterized_inventory, characterized_inventory])

        #add meta data and reorder
        all_characterized_inventory["activity_name"]=all_characterized_inventory["activity"].map(lambda x: self.act_time_mapping_reversed.get(x)[0])
        all_characterized_inventory["flow_name"] = all_characterized_inventory["flow"].apply(lambda x: bd.get_node(id=x)["name"])
        all_characterized_inventory = all_characterized_inventory[['date', 'amount', 'flow', 'flow_name','activity','activity_name', 'amount_sum']] 
        all_characterized_inventory.reset_index(drop=True, inplace=True)
        all_characterized_inventory.sort_values(by="date", ascending=True, inplace=True)
        
        return all_characterized_inventory, type_of_method, fixed_TH, TH
    
    
    def characterize_co(self, series, period: int = 100, cumulative=False) -> pd.DataFrame:
        """
        
        Radiative forcing functions of CO are taken from Levasseur et al 2010

        Units are watts/square meter/kilogram of ghg emissions

        Parameters
        ----------
        n20_decay_array:
        series : array-like
            A single row of the TimeSeries dataframe.
        period : int, optional
            Time period for calculation (number of years), by default 100
        cumulative : bool, optional
            Should the RF amounts be summed over time?

        Returns
        -------
        A TimeSeries dataframe with the following columns:
        - date: datetime64[s]
        - amount: float
        - flow: str
        - activity: str

        """
        co_decay_array = self.levasseur_dcfs["carbon monoxide"]

        date_beginning: np.datetime64 = series["date"].to_numpy()
        date_characterized: np.ndarray = date_beginning + np.arange(
            start=0, stop=period, dtype="timedelta64[Y]"
        ).astype("timedelta64[s]")


        decay_multiplier_y0 = [0] #no forcing in y0
        decay_multipliers_y1to100= [co_decay_array[key] for key in range(1,period) if key in co_decay_array]
        decay_multipliers = decay_multiplier_y0 + decay_multipliers_y1to100
        decay_multipliers= np.cumsum(decay_multipliers)


        forcing = pd.Series(data=series.amount * decay_multipliers, dtype="float64")
        if not cumulative:
            forcing = forcing.diff(periods=1).fillna(0)

        return pd.DataFrame(
            {
                "date": pd.Series(data=date_characterized, dtype="datetime64[s]"),
                "amount": forcing,
                "flow": series.flow,
                "activity": series.activity,
            }
        )
    
    def characterize_n2o(self, series, period: int = 100, cumulative=False) -> pd.DataFrame:
        """
        
        Radiative forcing functions of N2O are taken from Levasseur et al 2010

        Units are watts/square meter/kilogram of ghg emissions

        Parameters
        ----------
        n20_decay_array:
        series : array-like
            A single row of the TimeSeries dataframe.
        period : int, optional
            Time period for calculation (number of years), by default 100
        cumulative : bool, optional
            Should the RF amounts be summed over time?

        Returns
        -------
        A TimeSeries dataframe with the following columns:
        - date: datetime64[s]
        - amount: float
        - flow: str
        - activity: str

        """
        n2o_decay_array = self.levasseur_dcfs["nitrous oxide"]
        date_beginning: np.datetime64 = series["date"].to_numpy()
        date_characterized: np.ndarray = date_beginning + np.arange(
            start=0, stop=period, dtype="timedelta64[Y]"
        ).astype("timedelta64[s]")


        decay_multiplier_y0 = [0] #no forcing in y0
        decay_multipliers_y1to100= [n2o_decay_array[key] for key in range(1,period) if key in n2o_decay_array]
        decay_multipliers = decay_multiplier_y0 + decay_multipliers_y1to100
        decay_multipliers= np.cumsum(decay_multipliers)


        forcing = pd.Series(data=series.amount * decay_multipliers, dtype="float64")
        if not cumulative:
            forcing = forcing.diff(periods=1).fillna(0)

        return pd.DataFrame(
            {
                "date": pd.Series(data=date_characterized, dtype="datetime64[s]"),
                "amount": forcing,
                "flow": series.flow,
                "activity": series.activity,
            }
        )
            

    # def dynamic_characterization_from_giuseppe_levasseur(self,
    #                                                     characterized_inventory: pd.DataFrame,
    #                                                     dynIAM= "GWP",
    #                                                     cumulative: bool = False,
    #                                                     t0=None, 
    #                                                     TH: int | None =  100, 
    #                                                     characterize_dynamic_kwargs={}):
    #     """
    #     format of characterized_inventory is:
    #     | date | amount | flow | activity |
    #     |------|--------|------|----------|
    #     | 101  | 33     | 1    | 2        |
    #     | 102  | 32     | 1    | 2        |
    #     | 103  | 31     | 1    | 2        |

    #     """
    #     dyn_m={"GWP":"RadiativeForcing",
            
    #         #TODO make it run for GTP as well
    #         #    "GTP":"AGTP", #default is ar5
    #         #    "GTP base":"AGTP OP base",
    #         #    "GTP low":"AGTP OP low",
    #         #    "GTP high":"AGTP OP high",
    #         }
        
    #     assert dynIAM in dyn_m, "DynamicIAMethod not present, make sure name is correct and `create_climate_methods` was run"

    #     #set default start and calculate year of TH end
    #     th_zero=np.datetime64('now') if t0 is None else np.datetime64(t0)
    #     th_end=th_zero.astype('datetime64[Y]').astype(str).astype(int) + TH #convert to string first otherwhise gives years relative to POSIX time

    #     self.characterize_dynamic_G(dyn_m[dynIAM], cumulative) #, **characterize_dynamic_kwargs)

    #     return

    # def characterize_dynamic_G(self, string_method, cumulative = False): #, **characterize_dynamic_kwargs):
    #     print("test")
    #     return


# # old code
# from __future__ import print_function, unicode_literals
# from eight import *
# import os
# import numpy as np

# import pickle

# # ~CONSTANTS_PATH = pickle.load( open(os.path.join(os.path.dirname(__file__), 'constants.pkl'), "rb" ) )

# CONSTANTS_PATH = os.path.join(os.path.dirname(__file__), 'constants.pkl')



# #from https://github.com/brightway-lca/temporalis/blob/master/bw2temporalis/dyn_methods/timedependent_lca.py
# def time_dependent_LCA(self, demand, dynIAM='GWP',t0=None,TH=100, DynamicLCA_kwargs={}, characterize_dynamic_kwargs={}):
#     """calculate dynamic GWP or GTP for the functional unit and the time horizon indicated following the approach of Levausseur (2010, doi: 10.1021/es9030003).
#     It also consider climate effect of forest regrowth of biogenic CO2 (Cherubini 2011  doi: 10.1111/j.1757-1707.2011.01102.x)
#     assuming a rotation lenght of 100 yrs.
    
#     Note that TH is calculated on the basis of t0 i.e. also if first emissions occurs before t0 everything is characterzied till t0+TH. This imply
#     that, for instance, co2 emissions occuring before t0 but due to `demand` has an impact that his higher than   

    
# Args:
#     * *demand* (dict):  The functional unit. Same format as in LCA.
#     * *t0* (datetime,default = now): year 0 of the time horizon considered.
#     * *TH* (int,default =100): lenght of the time horizon in years. This TH is calculate on the basis of t0 i.e. also if first emissions occurs before t0 everyting is characterzied till t0=Th
#     * *dynIAM* (string, default='GWP'): Dynamic IA Method, can be 'GWP' or 'GTP'.
#     * *DynamicLCA_kwargs* (dict, default=None): optional argument to be passed for DynamicLCA.
#     * *characterize_dynamic_kwargs* (dict, default=None): optional arguments to be passed for characterize_dynamic.

#     """
#     CONSTANTS=pickle.load( open( CONSTANTS_PATH , "rb" ) )
    
#     dyn_m={"GWP":"RadiativeForcing",
#            "GTP":"AGTP", #default is ar5
#            "GTP base":"AGTP OP base",
#            "GTP low":"AGTP OP low",
#            "GTP high":"AGTP OP high",
#            }
#     assert dynIAM in dyn_m, "DynamicIAMethod not present, make sure name is correct and `create_climate_methods` was run"

#     #set default start and calculate year of TH end
#     th_zero=np.datetime64('now') if t0 is None else np.datetime64(t0)
#     th_end=th_zero.astype('datetime64[Y]').astype(str).astype(int) + TH #convert to string first otherwhise gives years relative to POSIX time

#     #calculate lca
#     dlca = DynamicLCA(demand, (dyn_m[dynIAM] , "worst case"),
#                       th_zero,
#                       **DynamicLCA_kwargs
#                      )
#     dyn_lca= self.characterized_inventory.characterize_dynamic(dyn_m[dynIAM],cumulative=False, **characterize_dynamic_kwargs)
#     #dyn_lca=([int(x) for x in dyn_lca[0]],dyn_lca[1]) #convert years to int, but better not to be consistent with resolution less than years

#     #pick denominator based on metric chosen
#     if dynIAM=='GWP':
#         co2_imp=CONSTANTS['co2_rf_td']
#     elif dynIAM=='GTP':
#         co2_imp=CONSTANTS['co2_agtp_ar5_td']
#     elif dynIAM=='GTP base':
#         co2_imp=CONSTANTS['co2_agtp_base_td']
#     elif dynIAM=='GTP low':
#         co2_imp=CONSTANTS['co2_agtp_low_td']
#     elif dynIAM=='GTP high':
#         co2_imp=CONSTANTS['co2_agtp_high_td']
        
#     #calculate lenght of th from first emission occuring
#     length=len([int(yr) for yr in dyn_lca[0] if int(yr) <= th_end])

#     #calculate agwp for demand and co2 and then gwp
#     res=np.trapz(x=dyn_lca[0][:length] , y=dyn_lca[1][:length]) / np.trapz(
#                  x=(co2_imp.times.astype('timedelta64[Y]').astype('int') + dyn_lca[0][0])[:length],
#                  y=co2_imp.values[:length])
    
#     return res


# #from https://github.com/brightway-lca/temporalis/blob/master/bw2temporalis/timeline.py#L91
# def characterize_dynamic(self, method, data=None, cumulative=True, stepped=False,bio_st_emis_yr=None,bio_st_decay=None,rot_stand=None):
#     """Characterize a Timeline object with a dynamic impact assessment method.
#     Return a nested list of year and impact
#     Args:
#         * *method* (tuple): The dynamic impact assessment method.
#         * *data* (Timeline object; default=None): ....
#         * *cumulative* (bool; default=True): when True return cumulative impact over time.
#         * *stepped* (bool; default=True):...
#         * *bio_st_emis_yr* (int; default=None): year when the biogenic carbon from stand is emitted, by default at yr=0.
#         * *bio_st_decay* (str; default=None): emission profile of biogenic carbon from stand .
#         * *rot_stand* (int; default=None): lenght of rotation of forest stands.

#     """
#     if method not in dynamic_methods:
#         raise ValueError(u"LCIA dynamic method %s not found" % method)
#     if data is None and not self.raw:
#         raise EmptyTimeline("No data to characterize")
#     meth = DynamicIAMethod(method)
#     self.method_data = meth.load()
#     #update biogenic carbon profile based on emission year and decay profile if passed
#     if any(v is not None for v in (bio_st_emis_yr,bio_st_decay,rot_stand)):                
#         self.method_data[('static_forest', 'C_biogenic')]="""def custom_co2bio_function(datetime):
#             from bw2temporalis.dyn_methods.metrics import {0}
#             from datetime import timedelta
#             import numpy as np
#             import collections
#             custom_co2bio_rf_td={0}("co2_biogenic", np.array((1.,)), np.array(({1} or 0,),dtype=('timedelta64[Y]')), 'Y', 1000,{3} or 100,'{2}' or 'delta') 
#             return_tuple = collections.namedtuple('return_tuple', ['dt', 'amount'])
#             return [return_tuple(d,v) for d,v in zip((datetime+custom_co2bio_rf_td.times.astype(timedelta)),custom_co2bio_rf_td.values)]""".format(method,bio_st_emis_yr,bio_st_decay,rot_stand)

#     method_functions = create_functions(self.method_data) #turns string of method data into a function, IMO total overkills but Giuseppe has programmed it like this
#     self.characterized = []
#     self.dp_groups=self._groupby_sum_by_flow(self.raw if data is None else data)

#     for obj in self.dp_groups:
#         # if obj.flow in method_functions:
#         self.characterized.extend([
#             grouped_dp(
#                 item.dt,
#                 obj.flow,
#                 item.amount * obj.amount
#             ) for item in method_functions[obj.flow](obj.dt)
#         ])
#         # else:
#             # continue
#             #GIU: I would skipe this,we save time plus memory, in groupby_sum_by_flow already skips datapoint not in method_data
#             #also more consistent in my opinion (the impact is not 0 but is simply not measurable)
            
#             # self.characterized.append(grouped_dp(
#                 # obj.dt,
#                 # obj.flow,
#                 # obj.amount * method_data.get(obj.flow, 0)
#             # ))
            
#     self.characterized.sort(key=lambda x: x.dt)

#     return self._summer(self.characterized, cumulative, stepped)
    
# #https://github.com/brightway-lca/temporalis/blob/master/bw2temporalis/dynamic_ia_methods.py#L88
# def create_functions(self, data=None):
#     """Take method data that defines functions in strings, and turn them into actual Python code. Returns a dictionary with flows as keys and functions as values."""
#     if data is None:
#         data = self.load()
#     prefix = "created_function_{}_".format(random_string())
#     functions = {}
#     for key, value in data.items():
#         if isinstance(value, str):
#             # Backwards compatibility
#             if '%s' in value:
#                 warnings.simplefilter('always', DeprecationWarning) #otherwise not warning and fail to pass test
#                 warnings.warn(
#                     "Functions can now be normal Python code; please change def %s() to def some_name().",
#                     DeprecationWarning
#                 )
#                 value = value % "created_function"
#             functions[key] = FunctionWrapper(value)
#     return functions
    